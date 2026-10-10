"""Phase B, stage ``report``: the questions answered in words, with citations.

1. Rebuild the findings (facts and charts), the ranking, the sensitivity checks and the
   approved plan.
2. Ask the model, through the gateway, to write the answers and a summary from the facts
   (never rows) with their evidence, citing fact ids; the summary may cite only the ranked
   highlights candidates. The run's personal values guard the payload.
3. Keep each sentence only if its numbers, citations and wording check out against the facts
   (``engine.analysis.narrate``); otherwise, and whenever the model is unavailable, the
   engine writes the answer from the facts' own statements.
4. Assemble the report (what the table is, how it was cleaned, the person's requests, the
   answers and charts, the evidence and how it was made) and save it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.analysis import report as report_doc
from insightfulcell.engine.analysis import stats
from insightfulcell.engine.analysis.narrate import NarrationProposal, narrate
from insightfulcell.engine.analysis.prompt import PROMPT_VERSION, SectionInput
from insightfulcell.engine.analysis.rank import QUALITY_KINDS
from insightfulcell.engine.analysis.signal import from_json as gate_from_json
from insightfulcell.engine.cleaning.operations import Decision, Operation
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.engine.planning.schema import response_schema
from insightfulcell.engine.ports import ReportRecord
from insightfulcell.engine.semantics import semantics_from_output
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, sensitive_values
from insightfulcell.pipeline.mine import findings_from_json
from insightfulcell.pipeline.sensitivity import rechecks_from_json
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class ReportStage:
    name = "report"
    version = "2"
    uses_run_inputs = True

    def __init__(self, *, model_identity: tuple[str, str] | None = None) -> None:
        self._identity = model_identity

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        provider, model = self._identity or (None, None)
        return {"prompt_version": PROMPT_VERSION, "provider": provider, "model": model}

    async def run(self, ctx: StageContext) -> StageResult:
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))
        operations = [
            Operation.model_validate(o) for o in await fetch_json(ctx, "clean_apply", "cleaning")
        ]
        found = await fetch_json(ctx, "mine", "findings")
        findings = findings_from_json(found)
        facts = {f.id: f for finding in findings for f in finding.facts}
        ranking = await fetch_json(ctx, "rank", "ranking")
        rechecks = rechecks_from_json(await fetch_json(ctx, "sensitivity", "rechecks"))
        gate = [gate_from_json(g) for g in await fetch_json(ctx, "features", "features")]
        # The summary may cite reliable or descriptive findings: the highlights first (best,
        # and covering more than one kind of finding), then the rest by rank.
        highlights: list[str] = list(ranking["highlights"])
        summary_facts = highlights + [
            r["fact_id"]
            for r in ranking["ranked"]
            if r["duplicate_of"] is None
            and r["fact_id"] not in highlights
            and facts[r["fact_id"]].tier != "inconclusive"
            and facts[r["fact_id"]].kind not in QUALITY_KINDS
        ]
        checks = {r.fact_id: report_doc.Check(outcome=r.outcome, note=r.note) for r in rechecks}
        applied = ctx.upstream["clean_apply"].output
        preparation = report_doc.Preparation(
            rows_before=int(applied.get("rows_before", 0)),
            rows_after=int(applied.get("rows_after", 0)),
            applied=tuple(
                report_doc.CleaningStep(label=op.label, column=op.column, rows=op.rows_affected)
                for op in operations
                if op.decision is Decision.ACCEPTED
            ),
            not_applied=sum(1 for op in operations if op.decision is not Decision.ACCEPTED),
            reviewed_by_person=ctx.inputs.cleaning_policy != "auto_safe",
        )
        sections = [
            SectionInput(
                analysis_id=f.analysis.id, question=f.analysis.question, facts=tuple(f.facts)
            )
            for f in findings
        ]
        parquet = await fetch(ctx, "ingest", "data", "data.parquet")
        semantics = semantics_from_output(await fetch_json(ctx, "ingest", "semantics"))
        guard = await asyncio.to_thread(sensitive_values, parquet, semantics)
        narration = await narrate(
            ctx.model,
            org_id=ctx.org_id,
            run_id=ctx.run_id,
            sections=sections,
            context=report_doc.context(plan, preparation),
            response_schema=response_schema(NarrationProposal),
            sensitive_values=guard,
            summary_facts=summary_facts,
            checks={k: v.note for k, v in checks.items()},
        )
        mined = ctx.upstream["mine"].output
        document = report_doc.build(
            dataset_name=ctx.inputs.dataset_name,
            plan=plan,
            findings=findings,
            narration=narration,
            preparation=preparation,
            rows=int(found.get("rows", preparation.rows_after)),
            highlights=highlights,
            checks=checks,
            evidence=report_doc.Evidence(
                tests=int(mined.get("tests", 0)),
                reliable=int(mined.get("reliable", 0)),
                alpha=stats.ALPHA,
                rechecked=len(rechecks),
            ),
            features=[
                report_doc.FeatureNote(
                    label=g.label, outcome=g.outcome, redundant_with=g.redundant_with
                )
                for g in gate
            ],
            derived_names={d["name"]: d["label"] for d in found.get("derived", [])},
        )
        data = document.model_dump(mode="json")
        await ctx.repository.save_report(
            ReportRecord(
                document=data,
                narration_source=narration.source,
                provider=narration.provider,
                model=narration.model,
                prompt_version=PROMPT_VERSION if narration.source != "engine" else None,
            )
        )
        key = await put_json(ctx, self.name, "report.json", data)
        answered = [s for s in document.sections if not s.skipped]
        return StageResult(
            output={
                "sections": len(document.sections),
                "answered": len(answered),
                "narration": narration.source,
                "provider": narration.provider,
                "model": narration.model,
                "fallback_reason": narration.fallback_reason,
                "sentences_kept": narration.kept,
                "sentences_dropped": narration.dropped,
                "written_by_engine": sum(1 for s in answered if s.written_by == "engine"),
            },
            artifacts={"report": key},
        )
