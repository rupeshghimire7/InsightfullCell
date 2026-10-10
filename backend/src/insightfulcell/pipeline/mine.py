"""Phase B, stage ``mine``: every question answered with facts, every test logged.

1. Build the analysis table: the cleaned data, the regions resolved by ``geo`` and the features
   that passed the gate (``engine.analysis.table``).
2. Run the nine miners over the plan's questions and the geographic ones; Benjamini-Hochberg at
   5% across every test of the run decides each finding's tier and wording.
3. Save the facts (immutable, with a content hash and provenance) and the test log together;
   write the analysis table and the findings (facts, charts, skipped questions) as artifacts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb

from insightfulcell.engine.analysis import stats
from insightfulcell.engine.analysis import table as analysis_table
from insightfulcell.engine.analysis.facts import ChartData, Fact
from insightfulcell.engine.analysis.miners import Derived, Finding, Mined, mine
from insightfulcell.engine.analysis.places import place_findings
from insightfulcell.engine.analysis.signal import GateResult, from_json, kept
from insightfulcell.engine.geo.reference import REFERENCE_SET
from insightfulcell.engine.geo.resolve import Resolution
from insightfulcell.engine.geo.resolve import from_json as place_from_json
from insightfulcell.engine.planning.language import ResolvedAnalysis, ResolvedPlan
from insightfulcell.engine.ports import TestRecord
from insightfulcell.engine.sql import literal
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_file, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class MineStage:
    name = "mine"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"alpha": stats.ALPHA, "reference_set": REFERENCE_SET}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "clean_apply", "data", "cleaned.parquet")
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))
        places = await load_places(ctx)
        features = await load_features(ctx)
        applied = ctx.upstream["clean_apply"].output
        provenance = {
            "stage": f"{self.name}@{self.version}",
            "data_hash": applied.get("data_hash"),
            "plan_hash": applied.get("plan_hash"),
            "reference_set": REFERENCE_SET,
            "alpha": stats.ALPHA,
        }
        out = ctx.workdir / "analysis.parquet"

        def compute() -> tuple[Mined, list[Derived], int]:
            with table(parquet, name="cleaned", threads=1) as con:
                result, derived = analyse(con, plan, places, features, seed=str(ctx.run_id),
                                          provenance=provenance)  # fmt: skip
                rows = _count(con)
                # The path is the worker's own.
                con.execute(f"COPY a TO {literal(str(out))} (FORMAT parquet)")
                return result, derived, rows

        mined, derived, rows = await asyncio.to_thread(compute)
        facts = [f for finding in mined.findings for f in finding.facts]
        await ctx.repository.save_findings(
            [{**f.model_dump(mode="json"), "content_hash": f.content_hash()} for f in facts],
            [_record(logged.test, logged.fact_id) for logged in mined.tests],
        )
        table_key = await put_file(ctx, self.name, "analysis.parquet", out)
        findings_key = await put_json(
            ctx,
            self.name,
            "findings.json",
            {
                "rows": rows,
                "threshold": mined.threshold,
                "derived": [
                    {"name": d.name, "label": d.label, "meaning": d.meaning.value,
                     "replaces": d.replaces, "origin": list(d.origin)}
                    for d in derived
                ],
                "findings": [finding_json(f) for f in mined.findings],
            },
        )  # fmt: skip
        answered = [f for f in mined.findings if not f.skipped]
        reliable = sum(1 for t in mined.tests if t.test.passed)
        return StageResult(
            output={
                "questions": len(mined.findings),
                "answered": len(answered),
                "skipped": len(mined.findings) - len(answered),
                "facts": len(facts),
                "charts": sum(1 for f in mined.findings if f.chart is not None),
                "tests": len(mined.tests),
                "reliable": reliable,
                "threshold": mined.threshold,
            },
            artifacts={"findings": findings_key, "table": table_key},
        )


def analyse(  # noqa: PLR0913  (keyword-only)
    con: duckdb.DuckDBPyConnection,
    plan: ResolvedPlan,
    places: Sequence[Resolution],
    features: Sequence[GateResult],
    *,
    seed: str,
    provenance: Mapping[str, Any] | None = None,
    only: frozenset[str] | None = None,
) -> tuple[Mined, list[Derived]]:
    """Build the analysis table ``a`` from view ``cleaned`` and mine it."""
    labels = {c.column: c.display_name or c.column for c in plan.dictionary}
    derived = analysis_table.build(
        con, source="cleaned", resolutions=places, features=features, labels=labels
    )
    extra, notes = place_findings(plan, places, derived)
    result = mine(
        con,
        plan,
        seed=seed,
        derived=derived,
        extra=extra,
        notes=notes if only is None else None,
        only=only,
        provenance=provenance,
    )
    return result, derived


async def load_places(ctx: StageContext) -> list[Resolution]:
    data = await fetch_json(ctx, "geo", "places")
    return [place_from_json(p) for p in data["places"]]


async def load_features(ctx: StageContext) -> list[GateResult]:
    return kept([from_json(g) for g in await fetch_json(ctx, "features", "features")])


def finding_json(finding: Finding) -> dict[str, Any]:
    return {
        "analysis": finding.analysis.model_dump(mode="json"),
        "facts": [f.model_dump(mode="json") for f in finding.facts],
        "chart": finding.chart.model_dump(mode="json") if finding.chart else None,
        "skipped": finding.skipped,
    }


def _record(test: stats.TestResult, fact_id: str | None) -> TestRecord:
    return TestRecord(
        purpose="primary",
        hypothesis=test.hypothesis,
        method=test.method,
        columns=test.columns,
        n=test.n,
        effect_name=test.effect_name,
        effect=test.effect,
        ci_low=test.ci_low,
        ci_high=test.ci_high,
        p_value=test.p_value,
        q_value=test.q_value,
        alpha=stats.ALPHA,
        passed=test.passed,
        fact_id=fact_id,
    )


def _count(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT count(*) FROM a").fetchone()
    return int(row[0]) if row else 0


def findings_from_json(found: Mapping[str, Any]) -> list[Finding]:
    """The findings artifact back into findings (the analysis travels with each)."""
    return [
        Finding(
            analysis=ResolvedAnalysis.model_validate(row["analysis"]),
            facts=[Fact.model_validate(f) for f in row["facts"]],
            chart=ChartData.model_validate(row["chart"]) if row.get("chart") else None,
            skipped=row.get("skipped"),
        )
        for row in found["findings"]
    ]
