"""Stage 3, ``understand``: the engine plans first, then a model refines it.

1. Build the schema card (masked; see ``engine.schema_card``).
2. **The engine plans**: the rule-based planner turns the card's columns into a complete,
   validated plan. This plan always exists and is the fallback for everything below.
3. Check the plan cache (same organisation, schema fingerprint and prompt version), unless the
   person asked for changes.
4. Otherwise send the card, the engine's draft and the person's requests to the model through
   the gateway (the run's personal values are the payload guard). It returns a revised,
   complete plan and an outcome for each request. Any failure (model off, over budget, timed
   out, invalid) keeps the engine's plan.
5. Validate every item; merge with the person's saved choices; apply the person's requests
   (the model's reading, or the engine's own reading of any request the model did not
   answer) and the analyses they removed.
6. Freeze the resolved plan as a hashed artifact. It, not the model, makes the run
   reproducible.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from insightfulcell.engine.planning import merge, rules, validator
from insightfulcell.engine.planning import requests as person_requests
from insightfulcell.engine.planning.language import (
    PLAN_LANGUAGE_VERSION,
    ProposedPlan,
    Provenance,
    RequestOutcome,
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
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, sensitive_values
from insightfulcell.pipeline.profile import column_records
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult

logger = logging.getLogger(__name__)

MAX_ANALYSES_WITH_REQUESTS: Final = 45


class UnderstandStage:
    name = "understand"
    version = "2"
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

        requests = person_requests.clean_requests(annotations.requests)
        removed = tuple(annotations.removed_analyses)

        # The engine plans first; the model, if any, refines that plan.
        rule_plan, _ = validator.validate(
            rules.plan(semantics, profile), semantics, profile, dataset_name=name
        )
        model_plan, provenance = await self._model_plan(
            ctx,
            parquet,
            semantics,
            profile,
            card,
            fingerprint,
            draft=rule_plan,
            requests=requests,
            removed=removed,
        )
        plan = merge.resolve(
            rules=rule_plan,
            model=model_plan,
            semantics=semantics,
            profile=profile,
            annotations=annotations,
            provenance=provenance,
        )
        plan = _apply_person(
            plan,
            requests=requests,
            removed=removed,
            model_plan=model_plan,
            semantics=semantics,
            profile=profile,
            dataset_name=name,
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
                "requests": len(plan.requests),
                "requests_applied": sum(
                    1 for r in plan.requests if r.status.value in {"added", "removed"}
                ),
                "removed_analyses": len(plan.removed_analyses),
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
        *,
        draft: ProposedPlan,
        requests: Sequence[str],
        removed: Sequence[str],
    ) -> tuple[ProposedPlan | None, Provenance]:
        name = ctx.inputs.dataset_name
        personal_choices = bool(requests or removed)

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

        # A plan made for someone's requests is not reused for anyone else's.
        cached = (
            None
            if personal_choices
            else await ctx.repository.find_cached_plan(fingerprint, PROMPT_VERSION)
        )
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
            user=user_prompt(
                card,
                draft=draft.model_dump(mode="json", exclude={"request_outcomes"}),
                requests=requests,
                removed=removed,
            ),
            response_model=ProposedPlan,
            response_schema=response_schema(ProposedPlan, [s.name for s in semantics]),
            sensitive_values=await asyncio.to_thread(sensitive_values, parquet, semantics),
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
        accepted = accepted.model_copy(
            update={"request_outcomes": list(reply.data.request_outcomes)}
        )
        if not personal_choices:  # the cache holds plans for the table alone
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


def _roles(plan: ResolvedPlan) -> dict[str, tuple[str, str | None, str | None]]:
    return {c.column: (c.role.value, c.display_name, c.unit) for c in plan.dictionary}


def _apply_person(  # noqa: PLR0913  (keyword-only)
    plan: ResolvedPlan,
    *,
    requests: Sequence[str],
    removed: Sequence[str],
    model_plan: ProposedPlan | None,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    dataset_name: str,
) -> ResolvedPlan:
    """The person's requests and removed analyses, applied to the merged plan.

    The model already acted on the requests it answered (its plan includes them). Any request
    without a model outcome (no model, or the model skipped it) is read by the engine and
    applied here, so every request gets an outcome. Removed analyses always go, whoever
    planned them."""
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    answered: dict[int, RequestOutcome] = {}
    if model_plan is not None:
        for item in model_plan.request_outcomes:
            if 1 <= item.index <= len(requests) and item.index not in answered:
                note = validator.tidy(item.note, 200) or "Handled by the planning model."
                answered[item.index] = RequestOutcome(
                    request=requests[item.index - 1],
                    status=item.status,
                    note=note,
                    source="model",
                )
    unanswered = [text for i, text in enumerate(requests, 1) if i not in answered]
    intents = person_requests.interpret(
        unanswered,
        semantics=semantics,
        profile=profile,
        names=names,
        target=plan.target.column if plan.target else None,
        dataset_name=dataset_name,
    )
    analyses, engine_outcomes = person_requests.apply(plan.analyses, intents, names)
    by_text = {o.request: o for o in engine_outcomes}
    outcomes = tuple(
        answered[i] if i in answered else by_text[text]
        for i, text in enumerate(requests, 1)
        if i in answered or text in by_text
    )

    removed_ids = set(removed)
    kept = [a for a in analyses if a.id not in removed_ids]
    gone = tuple(a for a in analyses if a.id in removed_ids)
    return plan.model_copy(
        update={
            "analyses": tuple(kept[:MAX_ANALYSES_WITH_REQUESTS]),
            "requests": outcomes,
            "removed_analyses": gone,
        }
    )
