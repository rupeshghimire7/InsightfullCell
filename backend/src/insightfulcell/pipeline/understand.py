"""Stage 3, ``understand``: the first model call. Masked schema card in, analysis plan out.

1. Build the schema card (masked; see ``engine.schema_card``).
2. Check the plan cache: same organisation, same schema fingerprint, same prompt version.
3. Otherwise ask the model through the gateway, with the run's personal values as the payload
   guard. Any failure (model off, over budget, timed out, invalid) falls back to rules.
4. Validate every item; merge with the rule plan and the person's saved choices.
5. Freeze the resolved plan as a hashed artifact. It, not the model, makes the run
   reproducible.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from insightfulcell.engine.planning import merge, rules, validator
from insightfulcell.engine.planning.language import (
    PLAN_LANGUAGE_VERSION,
    ProposedPlan,
    Provenance,
    ResolvedPlan,
    content_hash,
)
from insightfulcell.engine.planning.prompt import (
    PROMPT_ID,
    PROMPT_VERSION,
    system_prompt,
    user_prompt,
)
from insightfulcell.engine.planning.schema import response_schema
from insightfulcell.engine.ports import ModelRequest, ModelUnavailable, PlanRecord, Purpose
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.schema_card import build_card, estimate_tokens, fingerprint_parts
from insightfulcell.engine.semantics import ColumnSemantics, semantics_from_output
from insightfulcell.engine.sql import ident
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.profile import column_records
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult

logger = logging.getLogger(__name__)

SENSITIVE_VALUES_PER_COLUMN: Final = 2_000


class UnderstandStage:
    name = "understand"
    version = "1"
    uses_run_inputs = True

    def __init__(
        self, *, card_token_budget: int = 9_000, model_identity: tuple[str, str] | None = None
    ) -> None:
        self._budget = card_token_budget
        self._identity = model_identity

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        provider, model = self._identity or (None, None)
        return {
            "prompt_version": PROMPT_VERSION,
            "plan_language": PLAN_LANGUAGE_VERSION,
            "card_budget": self._budget,
            "provider": provider,
            "model": model,
        }

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "ingest", "data", "data.parquet")
        semantics = semantics_from_output(await fetch_json(ctx, "ingest", "semantics"))
        profile = Profile.model_validate(await fetch_json(ctx, "profile", "profile"))
        annotations = merge.Annotations.model_validate(ctx.inputs.annotations or {})
        name = ctx.inputs.dataset_name
        confirmed = {
            column: {"display_name": note.display_name or "", "unit": note.unit or ""}
            for column, note in annotations.columns.items()
            if note.display_name or note.unit
        }
        card = build_card(
            semantics, profile, dataset_name=name, token_budget=self._budget, confirmed=confirmed
        )
        fingerprint = content_hash(fingerprint_parts(semantics))

        rule_plan, _ = validator.validate(
            rules.plan(semantics, profile), semantics, profile, dataset_name=name
        )
        model_plan, provenance = await self._model_plan(
            ctx, parquet, semantics, profile, card, fingerprint
        )
        plan = merge.resolve(
            rules=rule_plan,
            model=model_plan,
            semantics=semantics,
            profile=profile,
            annotations=annotations,
            provenance=provenance,
        )
        plan_json = plan.model_dump(mode="json")
        await ctx.repository.save_plan(
            PlanRecord(
                kind="resolved",
                source=provenance.source,
                schema_fingerprint=fingerprint,
                prompt_version=provenance.prompt_version,
                provider=provenance.provider,
                model=provenance.model,
                plan=plan_json,
                plan_hash=plan.digest(),
                rejected=[r.model_dump(mode="json") for r in provenance.rejected],
                input_card=card,
                fallback_reason=provenance.fallback_reason,
            )
        )
        await ctx.repository.save_columns(column_records(semantics, profile, _roles(plan)))
        plan_key = await put_json(ctx, self.name, "plan.json", plan_json)
        card_key = await put_json(ctx, self.name, "schema_card.json", card)
        return StageResult(
            output={
                "plan_source": provenance.source,
                "provider": provenance.provider,
                "model": provenance.model,
                "fallback_reason": provenance.fallback_reason,
                "target": plan.target.column if plan.target else None,
                "features": len(plan.features),
                "analyses": len(plan.analyses),
                "charts": len(plan.charts),
                "rejected": len(provenance.rejected),
                "card_tokens": card["detail"]["estimated_tokens"],
                "plan_hash": plan.digest(),
                "summary": plan.dataset_summary,
            },
            artifacts={"plan": plan_key, "schema_card": card_key},
        )

    async def _model_plan(  # noqa: PLR0913, PLR0917
        self,
        ctx: StageContext,
        parquet: Path,
        semantics: Sequence[ColumnSemantics],
        profile: Profile,
        card: Mapping[str, Any],
        fingerprint: str,
    ) -> tuple[ProposedPlan | None, Provenance]:
        name = ctx.inputs.dataset_name

        def provenance(
            source: str,
            *,
            fallback: str | None = None,
            rejected: Sequence[Any] = (),
            provider: str | None = None,
            model: str | None = None,
        ) -> Provenance:
            return Provenance(
                source=source,
                provider=provider,
                model=model,
                prompt_version=PROMPT_VERSION if source != "rules" else None,
                fallback_reason=fallback,
                schema_fingerprint=fingerprint,
                rejected=tuple(rejected),
            )

        cached = await ctx.repository.find_cached_plan(fingerprint, PROMPT_VERSION)
        if cached is not None:
            try:
                proposed = ProposedPlan.model_validate(cached.plan)
            except ValueError:
                proposed = None
            if proposed is not None:
                accepted, rejected = validator.validate(
                    proposed, semantics, profile, dataset_name=name
                )
                return accepted, provenance(
                    "cache", rejected=rejected, provider=cached.provider, model=cached.model
                )

        if ctx.model.identity is None:
            return None, provenance("rules", fallback="disabled")
        request = ModelRequest(
            purpose=Purpose.PLAN,
            org_id=ctx.org_id,
            run_id=ctx.run_id,
            prompt_id=PROMPT_ID,
            prompt_version=PROMPT_VERSION,
            system=system_prompt(),
            user=user_prompt(card),
            response_model=ProposedPlan,
            response_schema=response_schema(ProposedPlan, [s.name for s in semantics]),
            sensitive_values=await asyncio.to_thread(_sensitive_values, parquet, semantics),
        )
        try:
            reply = await ctx.model.complete(request)
        except ModelUnavailable as exc:
            logger.info(
                "planner fell back to rules",
                extra={"run_id": str(ctx.run_id), "reason": exc.reason},
            )
            return None, provenance("rules", fallback=exc.reason)
        assert isinstance(reply.data, ProposedPlan)  # noqa: S101  (the request's response model)
        accepted, rejected = validator.validate(reply.data, semantics, profile, dataset_name=name)
        await ctx.repository.save_plan(
            PlanRecord(
                kind="proposal",
                source="model",
                schema_fingerprint=fingerprint,
                prompt_version=PROMPT_VERSION,
                provider=reply.provider,
                model=reply.model,
                plan=accepted.model_dump(mode="json"),
                plan_hash=content_hash(accepted.model_dump(mode="json")),
                rejected=[r.model_dump(mode="json") for r in rejected],
            )
        )
        logger.info(
            "planner answered",
            extra={
                "run_id": str(ctx.run_id),
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
                "card_tokens": estimate_tokens(card),
                "rejected": len(rejected),
            },
        )
        return accepted, provenance(
            "model", rejected=rejected, provider=reply.provider, model=reply.model
        )


def _sensitive_values(parquet: Path, semantics: Sequence[ColumnSemantics]) -> frozenset[str]:
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


def _roles(plan: ResolvedPlan) -> dict[str, tuple[str, str | None, str | None]]:
    return {c.column: (c.role.value, c.display_name, c.unit) for c in plan.dictionary}
