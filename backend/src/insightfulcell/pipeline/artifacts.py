"""Reading and writing stage artifacts (the ArtifactStore port) and the run's DuckDB view."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import duckdb

from insightfulcell.engine import sql
from insightfulcell.engine.semantics import ColumnSemantics
from insightfulcell.engine.sql import ident
from insightfulcell.pipeline.stage import StageContext

SENSITIVE_VALUES_PER_COLUMN: Final = 2_000


def upstream_artifact(ctx: StageContext, stage: str, name: str) -> str:
    result = ctx.upstream.get(stage)
    if result is None or name not in result.artifacts:
        raise LookupError(f"stage {stage!r} produced no artifact {name!r}")
    return result.artifacts[name]


async def fetch(ctx: StageContext, stage: str, name: str, filename: str) -> Path:
    dest = ctx.workdir / filename
    await ctx.store.download_file(upstream_artifact(ctx, stage, name), dest)
    return dest


async def fetch_json(ctx: StageContext, stage: str, name: str) -> Any:
    path = await fetch(ctx, stage, name, f"{stage}-{name}.json")
    return json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))


async def put_file(ctx: StageContext, stage: str, name: str, path: Path) -> str:
    key = ctx.artifact_key(stage, name)
    await ctx.store.upload_file(path, key)
    return key


async def put_json(ctx: StageContext, stage: str, name: str, value: Any) -> str:
    path = ctx.workdir / f"out-{stage}-{name}"
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    await asyncio.to_thread(path.write_text, text, encoding="utf-8")
    key = ctx.artifact_key(stage, name)
    await ctx.store.upload_file(path, key)
    return key


@contextmanager
def table(
    parquet: Path, *, name: str = "t", threads: int = 2
) -> Iterator[duckdb.DuckDBPyConnection]:
    """A bounded DuckDB with the run's typed data as view ``name``. Phase B's statistics use
    one thread: window functions (bands) then break ties the same way on every run, so a
    retry computes the same facts."""
    con = sql.connect(threads=threads)
    try:
        sql.open_table(con, parquet, name=name)
        yield con
    finally:
        con.close()


def sensitive_values(parquet: Path, semantics: Sequence[ColumnSemantics]) -> frozenset[str]:
    """Distinct values of personal-data columns: the gateway refuses any payload holding one.
    They stay in this process and are never stored."""
    flagged = [s.name for s in semantics if s.personal_data is not None]
    if not flagged:
        return frozenset()
    values: set[str] = set()
    with table(parquet) as con:
        for column in flagged:
            c = ident(column)  # quoted: column names come from the user's file
            rows = con.execute(
                f"SELECT DISTINCT trim(CAST({c} AS VARCHAR)) FROM t WHERE {c} IS NOT NULL "  # noqa: S608
                "LIMIT ?",
                [SENSITIVE_VALUES_PER_COLUMN],
            ).fetchall()
            values.update(str(v) for (v,) in rows if v and len(str(v)) >= 4)
    return frozenset(values)
