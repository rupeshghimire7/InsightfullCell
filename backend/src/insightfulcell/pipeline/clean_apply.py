"""Phase B, stage ``clean_apply``: the approved cleaning applied to the parsed upload.

Reads the plan and cleaning plan as the person approved them (their latest revisions in the
database; the Phase A artifacts when there is no database, as in tests and the CLI) and applies
exactly the accepted operations with the same SQL the recipe uses. Writes two tables:

- the **cleaned** data, at ``silver/{org}/{version}/{plan_hash}.parquet``: keyed by the file
  and the cleaning, so it can always be rebuilt from the upload plus the plan;
- the **minimally cleaned** data: the same file with only the accepted lossless operations,
  which the sensitivity check re-tests the top findings on.

The upload itself is never changed. Features are added later, only if they pass the gate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb

from insightfulcell.engine.cleaning.operations import Decision, Operation, cleaned_select
from insightfulcell.engine.planning.language import ResolvedPlan, content_hash
from insightfulcell.engine.sql import literal
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class CleanApplyStage:
    name = "clean_apply"
    version = "2"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"sha256": source.sha256}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "ingest", "data", "data.parquet")
        plan_json = await ctx.repository.latest_resolved_plan()
        if plan_json is None:
            plan_json = await fetch_json(ctx, "understand", "plan")
        ops_json = await ctx.repository.approved_cleaning()
        if ops_json is None:
            ops_json = await fetch_json(ctx, "clean_plan", "cleaning_plan")
        plan = ResolvedPlan.model_validate(plan_json)
        # Anything still undecided is not applied (an approved plan has none).
        operations = [
            op
            if op.decision is not Decision.PENDING
            else op.model_copy(update={"decision": Decision.REJECTED})
            for op in (Operation.model_validate(o) for o in ops_json)
        ]
        accepted = [op for op in operations if op.decision is Decision.ACCEPTED]
        lossless = [
            op if not op.lossy else op.model_copy(update={"decision": Decision.REJECTED})
            for op in operations
        ]
        lossy_applied = sum(1 for op in accepted if op.lossy)
        cleaned = ctx.workdir / "cleaned.parquet"
        minimal = ctx.workdir / "minimal.parquet"

        def compute() -> tuple[int, int, int]:
            with table(parquet) as con:
                before = _count(con, "t")
                after = _write(con, operations, cleaned)
                kept = _write(con, lossless, minimal) if lossy_applied else after
                return before, after, kept

        before, after, minimal_rows = await asyncio.to_thread(compute)
        digest = content_hash(
            {"plan": plan.digest(), "operations": [op.model_dump(mode="json") for op in accepted]}
        )
        data_key = ctx.silver_key(digest)
        await ctx.store.upload_file(cleaned, data_key)
        artifacts = {"data": data_key}
        if lossy_applied:
            minimal_key = ctx.artifact_key(self.name, "minimal.parquet")
            await ctx.store.upload_file(minimal, minimal_key)
            artifacts["minimal"] = minimal_key
        artifacts["plan"] = await put_json(
            ctx, self.name, "plan.json", plan.model_dump(mode="json")
        )
        artifacts["cleaning"] = await put_json(
            ctx, self.name, "cleaning.json", [op.model_dump(mode="json") for op in operations]
        )
        return StageResult(
            output={
                "rows_before": before,
                "rows_after": after,
                "operations_applied": len(accepted),
                "operations_not_applied": len(operations) - len(accepted),
                "lossy_applied": lossy_applied,
                "minimal_rows": minimal_rows,
                "plan_hash": plan.digest(),
                "data_hash": digest,
            },
            artifacts=artifacts,
        )


def _count(con: duckdb.DuckDBPyConnection, name: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {name}").fetchone()  # noqa: S608  (our own name)
    return int(row[0]) if row else 0


def _write(con: duckdb.DuckDBPyConnection, operations: Sequence[Operation], out: Path) -> int:
    names = [row[0] for row in con.execute("DESCRIBE t").fetchall()]
    query = cleaned_select(names, operations, table="t")
    con.execute(f"CREATE OR REPLACE TABLE cleaned AS {query}")
    # The path is the worker's own.
    con.execute(f"COPY cleaned TO {literal(str(out))} (FORMAT parquet)")
    return _count(con, "cleaned")
