"""Plan use cases for the API: read a run's plan, revise it, decide it, export the recipe.

At the review gate a person either:

- **revises** the plan: their cleaning decisions so far, column edits, removed questions and
  requests in their own words are saved, and the run goes back to Phase A, which plans again
  (the engine first, then the model) and returns to review; or
- **decides** (approves) it: a new cleaning plan revision (who decided, when), a new resolved
  plan revision when names, units, exclusions, the target or features changed, and the
  person's choices saved for the dataset's later runs. Then the run leaves the gate for
  Phase B.

Each in one transaction; ``If-Match`` (the run's row version) and the cleaning revision the
person saw guard against acting on a stale view.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import (
    AnalysisPlan,
    CleaningOp,
    CleaningPlan,
    DatasetPreferences,
    DatasetVersion,
    Run,
    RunColumn,
    RunStage,
)
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import (
    CleaningPlanChanged,
    InvalidDecision,
    PlanNotReady,
    RunChanged,
)
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.domain.runs import EventType, Phase, Queue, RunStatus, require_status
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.engine import recipe
from insightfulcell.engine.cleaning.operations import CATALOGUE, Decision, Operation
from insightfulcell.engine.planning.language import CleaningKind, ResolvedPlan, content_hash
from insightfulcell.engine.planning.merge import Annotations, ColumnAnnotation, apply_annotations
from insightfulcell.engine.planning.requests import clean_requests
from insightfulcell.queue.port import JobQueue, PhaseJob
from insightfulcell.services import run_store
from insightfulcell.services.analysis_store import insert_cleaning_plan
from insightfulcell.services.dto import RunDetail

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ColumnEdit:
    name: str
    display_name: str | None = None
    unit: str | None = None
    excluded: bool | None = None


@dataclass(frozen=True, slots=True)
class PlanDecision:
    cleaning_revision: int
    decisions: Mapping[str, bool]  # operation id -> accept
    columns: Sequence[ColumnEdit] = ()
    target: str | None = None
    no_target: bool = False
    disabled_features: Sequence[str] | None = None
    #: Revisions only (None keeps what is saved).
    removed_analyses: Sequence[str] | None = None
    requests: Sequence[str] | None = None


@dataclass(frozen=True, slots=True)
class CleaningView:
    revision: int
    policy: str
    status: str
    decided_by_you: bool
    decided_at: Any
    operations: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PlanView:
    run: RunDetail
    revision: int
    source: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    fallback_reason: str | None
    plan: dict[str, Any]
    rejected: list[dict[str, Any]]
    model_input: dict[str, Any] | None
    columns: list[dict[str, Any]]
    cleaning: CleaningView | None
    preferences_version: int = 0
    notes: list[str] = field(default_factory=list)


class PlanService:
    def __init__(self, db: TenantDatabase, queue: JobQueue) -> None:
        self._db = db
        self._queue = queue

    # ------------------------------------------------------------------ read

    async def get_plan(self, principal: Principal, run_id: UUID) -> PlanView:
        require(principal, Action.RUN_READ)
        async with self._db.transaction(principal) as session:
            detail = await run_store.run_detail(session, principal.org_id, run_id)
            resolved = await _latest_plan(session, principal.org_id, run_id)
            if resolved is None:
                raise PlanNotReady(
                    "This analysis has not planned its work yet.",
                    hint="Wait for the 'Plan the analysis' step to finish.",
                )
            first = (
                await session.execute(
                    select(AnalysisPlan.input_card).where(
                        AnalysisPlan.run_id == run_id,
                        AnalysisPlan.org_id == principal.org_id,
                        AnalysisPlan.kind == "resolved",
                        AnalysisPlan.revision == 1,
                    )
                )
            ).scalar_one_or_none()
            columns = (
                await session.execute(
                    select(RunColumn)
                    .where(RunColumn.run_id == run_id, RunColumn.org_id == principal.org_id)
                    .order_by(RunColumn.position)
                )
            ).scalars()
            cleaning = await _latest_cleaning(session, principal.org_id, run_id)
            prefs_version = (
                await session.execute(
                    select(DatasetPreferences.row_version).where(
                        DatasetPreferences.dataset_id == detail.run.dataset_id,
                        DatasetPreferences.org_id == principal.org_id,
                    )
                )
            ).scalar_one_or_none()
            cleaning_view = None
            if cleaning is not None:
                plan_row, ops = cleaning
                cleaning_view = CleaningView(
                    revision=plan_row.revision,
                    policy=plan_row.policy,
                    status=plan_row.status,
                    decided_by_you=plan_row.decided_by == principal.user_id,
                    decided_at=plan_row.decided_at,
                    operations=[_op_dict(op) for op in ops],
                )
            return PlanView(
                run=detail,
                revision=resolved.revision,
                source=resolved.source,
                provider=resolved.provider,
                model=resolved.model,
                prompt_version=resolved.prompt_version,
                fallback_reason=resolved.fallback_reason,
                plan=resolved.plan,
                rejected=list(resolved.rejected),
                model_input=first,
                columns=[_column_dict(c) for c in columns],
                cleaning=cleaning_view,
                preferences_version=prefs_version or 0,
            )

    # ------------------------------------------------------------------ revise

    async def revise(
        self,
        principal: Principal,
        run_id: UUID,
        decision: PlanDecision,
        *,
        if_match: int | None = None,
    ) -> RunDetail:
        """Save the person's changes and plan again: the run goes back to Phase A (finished
        steps whose inputs did not change are reused) and returns to the review gate."""
        require(principal, Action.CLEANING_DECIDE)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            if if_match is not None and if_match != run.row_version:
                raise RunChanged(
                    "Someone or something changed this analysis after you loaded it.",
                    hint="Reload the page, check the analysis, and try again.",
                )
            require_status(RunStatus(run.status), {RunStatus.AWAITING_REVIEW}, "revised")
            latest = await _latest_cleaning(session, principal.org_id, run_id)
            resolved = await _latest_plan(session, principal.org_id, run_id)
            if latest is None or resolved is None:
                raise PlanNotReady("This analysis has no plan to revise.")
            plan_row, ops = latest
            if decision.cleaning_revision != plan_row.revision:
                raise CleaningPlanChanged(
                    "The cleaning plan changed since you loaded it.",
                    hint="Reload the page to see the latest changes, then try again.",
                )
            personal = await _personal_columns(session, principal.org_id, run_id)
            _check_decisions(ops, decision.decisions, personal)
            plan = ResolvedPlan.model_validate(resolved.plan)
            known = {a.id for a in (*plan.analyses, *plan.removed_analyses)}
            if set(decision.removed_analyses or ()) - known:
                raise InvalidDecision(
                    "A removed question is not part of this plan.",
                    hint="Reload the page and try again.",
                )
            annotations = await self._save_preferences(session, principal, run, decision, resolved)
            inputs = dict(run.inputs or {})
            inputs["annotations"] = annotations.model_dump(mode="json")
            inputs["cleaning_decisions"] = {k: bool(v) for k, v in decision.decisions.items()}
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.QUEUED,
                sources={RunStatus.AWAITING_REVIEW},
                values={"phase": Phase.A.value, "stage": None, "attempts": 0, "inputs": inputs},
            )
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                type_=EventType.REVIEW_REVISION_REQUESTED,
                data={
                    "requests": len(annotations.requests),
                    "removed": len(annotations.removed_analyses),
                },
            )
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        try:
            await self._queue.defer_phase(
                PhaseJob(run_id, principal.org_id, Phase.A, Queue(detail.run.queue))
            )
        except Exception:
            logger.warning(
                "could not defer phase job; the reconciler will retry",
                extra={"run_id": str(run_id)},
                exc_info=True,
            )
        return detail

    # ------------------------------------------------------------------ decide

    async def decide(
        self,
        principal: Principal,
        run_id: UUID,
        decision: PlanDecision,
        *,
        if_match: int | None = None,
    ) -> RunDetail:
        require(principal, Action.CLEANING_DECIDE)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            if if_match is not None and if_match != run.row_version:
                raise RunChanged(
                    "Someone or something changed this analysis after you loaded it.",
                    hint="Reload the page, check the analysis, and decide again.",
                )
            require_status(RunStatus(run.status), {RunStatus.AWAITING_REVIEW}, "approved")
            latest = await _latest_cleaning(session, principal.org_id, run_id)
            resolved = await _latest_plan(session, principal.org_id, run_id)
            if latest is None or resolved is None:
                raise PlanNotReady("This analysis has no plan to decide on.")
            plan_row, ops = latest
            if decision.cleaning_revision != plan_row.revision:
                raise CleaningPlanChanged(
                    "The cleaning plan changed since you loaded it.",
                    hint="Reload the page to see the latest changes, then decide again.",
                )
            personal = await _personal_columns(session, principal.org_id, run_id)
            decided = _apply_decisions(ops, decision.decisions, personal)
            accepted = sum(1 for op in decided if op["decision"] == Decision.ACCEPTED.value)
            await insert_cleaning_plan(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                policy=plan_row.policy,
                status="approved",
                plan_hash=_hash(decided),
                operations=decided,
                decided_by=principal.user_id,
            )
            annotations = await self._save_preferences(session, principal, run, decision, resolved)
            await self._revise_plan(session, principal, run, resolved, annotations)
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.QUEUED,
                sources={RunStatus.AWAITING_REVIEW},
                values={"phase": Phase.B.value, "stage": None, "attempts": 0},
            )
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                type_=EventType.REVIEW_APPROVED,
                data={
                    "cleaning_revision": plan_row.revision + 1,
                    "accepted": accepted,
                    "rejected": len(decided) - accepted,
                },
            )
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        try:
            await self._queue.defer_phase(
                PhaseJob(run_id, principal.org_id, Phase.B, Queue(detail.run.queue))
            )
        except Exception:
            logger.warning(
                "could not defer phase job; the reconciler will retry",
                extra={"run_id": str(run_id)},
                exc_info=True,
            )
        return detail

    async def _save_preferences(
        self,
        session: AsyncSession,
        principal: Principal,
        run: Run,
        decision: PlanDecision,
        resolved: AnalysisPlan,
    ) -> Annotations:
        plan = ResolvedPlan.model_validate(resolved.plan)
        known = {c.column: c for c in plan.dictionary}
        row = (
            await session.execute(
                select(DatasetPreferences)
                .where(
                    DatasetPreferences.dataset_id == run.dataset_id,
                    DatasetPreferences.org_id == principal.org_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        current = Annotations.model_validate(row.data if row is not None else {})
        columns = dict(current.columns)
        for edit in decision.columns:
            if edit.name not in known:
                raise InvalidDecision(
                    "A column in the decision is not in this analysis.",
                    hint="Reload the page and try again.",
                )
            entry = known[edit.name]
            if edit.excluded is False and entry.personal_data:
                raise InvalidDecision(
                    "Columns with personal data always stay out of the analysis.",
                    hint="Leave personal-data columns excluded.",
                )
            previous = columns.get(edit.name, ColumnAnnotation())
            columns[edit.name] = ColumnAnnotation(
                display_name=_tidy(edit.display_name, 60) or previous.display_name,
                unit=_tidy(edit.unit, 16) if edit.unit is not None else previous.unit,
                excluded=edit.excluded if edit.excluded is not None else previous.excluded,
            )
        if decision.target is not None and decision.target not in known:
            raise InvalidDecision("The chosen target is not a column of this analysis.")
        if decision.target and known[decision.target].personal_data:
            raise InvalidDecision("A column with personal data cannot be the target.")
        updated = Annotations(
            columns=columns,
            target=decision.target
            if decision.target
            else (None if decision.no_target else current.target),
            no_target=decision.no_target,
            disabled_features=tuple(
                decision.disabled_features
                if decision.disabled_features is not None
                else current.disabled_features
            ),
            removed_analyses=tuple(
                dict.fromkeys(
                    decision.removed_analyses
                    if decision.removed_analyses is not None
                    else current.removed_analyses
                )
            ),
            requests=(
                clean_requests(decision.requests)
                if decision.requests is not None
                else current.requests
            ),
        )
        data = updated.model_dump(mode="json")
        if row is None:
            session.add(
                DatasetPreferences(
                    org_id=principal.org_id,
                    dataset_id=run.dataset_id,
                    data=data,
                    updated_by=principal.user_id,
                )
            )
        else:
            await session.execute(
                update(DatasetPreferences)
                .where(
                    DatasetPreferences.dataset_id == run.dataset_id,
                    DatasetPreferences.org_id == principal.org_id,
                )
                .values(
                    data=data,
                    row_version=DatasetPreferences.row_version + 1,
                    updated_by=principal.user_id,
                    updated_at=func.now(),
                )
            )
        return updated

    async def _revise_plan(
        self,
        session: AsyncSession,
        principal: Principal,
        run: Run,
        resolved: AnalysisPlan,
        annotations: Annotations,
    ) -> None:
        plan = ResolvedPlan.model_validate(resolved.plan)
        columns = list(
            (
                await session.execute(
                    select(RunColumn).where(
                        RunColumn.run_id == run.id, RunColumn.org_id == principal.org_id
                    )
                )
            ).scalars()
        )
        distinct = {c.name: int((c.profile or {}).get("distinct", 0)) for c in columns}
        revised = apply_annotations(plan, annotations, distinct=distinct)
        if revised.digest() == plan.digest():
            return
        session.add(
            AnalysisPlan(
                id=new_uuid7(),
                org_id=principal.org_id,
                run_id=run.id,
                dataset_id=run.dataset_id,
                kind="resolved",
                revision=resolved.revision + 1,
                source="person",
                schema_fingerprint=resolved.schema_fingerprint,
                prompt_version=resolved.prompt_version,
                provider=resolved.provider,
                model=resolved.model,
                plan=revised.model_dump(mode="json"),
                plan_hash=revised.digest(),
                rejected=list(resolved.rejected),
                created_by=principal.user_id,
            )
        )
        for entry in revised.dictionary:
            await session.execute(
                update(RunColumn)
                .where(
                    RunColumn.run_id == run.id,
                    RunColumn.org_id == principal.org_id,
                    RunColumn.name == entry.column,
                )
                .values(display_name=entry.display_name, unit=entry.unit, role=entry.role.value)
            )

    # ------------------------------------------------------------------ recipe

    async def recipe(self, principal: Principal, run_id: UUID) -> tuple[str, str]:
        """(file name, Python source) of the run's recipe."""
        require(principal, Action.RUN_READ)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id)
            resolved = await _latest_plan(session, principal.org_id, run_id)
            cleaning = await _latest_cleaning(session, principal.org_id, run_id)
            if resolved is None or cleaning is None:
                raise PlanNotReady(
                    "This analysis has no cleaning plan yet.",
                    hint="Wait for the 'Propose cleaning' step to finish.",
                )
            ingest = (
                await session.execute(
                    select(RunStage.output).where(
                        RunStage.run_id == run_id,
                        RunStage.org_id == principal.org_id,
                        RunStage.name == "ingest",
                    )
                )
            ).scalar_one()
            version = (
                await session.execute(
                    select(DatasetVersion.format).where(
                        DatasetVersion.id == run.dataset_version_id,
                        DatasetVersion.org_id == principal.org_id,
                    )
                )
            ).scalar_one()
            names = await run_store.dataset_names(session, principal.org_id, [run.dataset_id])
        plan_row, ops = cleaning
        source = recipe.render(
            dataset_name=names.get(run.dataset_id, "dataset"),
            run_id=str(run_id),
            source_format=version,
            columns=[(c["name"], c["type"]) for c in ingest.get("columns", [])],
            read_as_text=bool(ingest.get("read_as_text")),
            operations=[Operation.model_validate(_op_dict(op)) for op in ops],
            plan=ResolvedPlan.model_validate(resolved.plan),
            cleaning_revision=plan_row.revision,
            cleaning_status=plan_row.status,
        )
        return f"insightfulcell-recipe-{str(run_id)[:8]}.py", source


# --------------------------------------------------------------------------- helpers


async def _latest_plan(session: AsyncSession, org_id: UUID, run_id: UUID) -> AnalysisPlan | None:
    return (
        await session.execute(
            select(AnalysisPlan)
            .where(
                AnalysisPlan.run_id == run_id,
                AnalysisPlan.org_id == org_id,
                AnalysisPlan.kind == "resolved",
            )
            .order_by(AnalysisPlan.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_cleaning(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> tuple[CleaningPlan, list[CleaningOp]] | None:
    plan = (
        await session.execute(
            select(CleaningPlan)
            .where(CleaningPlan.run_id == run_id, CleaningPlan.org_id == org_id)
            .order_by(CleaningPlan.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if plan is None:
        return None
    ops = list(
        (
            await session.execute(
                select(CleaningOp)
                .where(CleaningOp.plan_id == plan.id, CleaningOp.org_id == org_id)
                .order_by(CleaningOp.position)
            )
        ).scalars()
    )
    return plan, ops


def _op_dict(op: CleaningOp) -> dict[str, Any]:
    return {
        "id": op.op_id,
        "kind": op.kind,
        "column": op.column_name,
        "params": op.params,
        "lossy": op.lossy,
        "default": op.default_mode,
        "decision": op.decision,
        "reason": op.reason,
        "source": op.source,
        "order": _order(op.kind),
        "rows_affected": op.rows_affected,
        "values_lost": op.values_lost,
        "preview": op.preview,
        "notice": op.notice,
    }


def _order(kind: str) -> int:
    return CATALOGUE[CleaningKind(kind)].order


def _column_dict(c: RunColumn) -> dict[str, Any]:
    return {
        "position": c.position,
        "name": c.name,
        "physical_type": c.physical_type,
        "meaning": c.meaning,
        "confidence": c.confidence,
        "personal_data": c.personal_data,
        "unit": c.unit,
        "display_name": c.display_name,
        "role": c.role,
        "flags": list(c.flags or []),
        "profile": c.profile,
    }


async def _personal_columns(session: AsyncSession, org_id: UUID, run_id: UUID) -> set[str]:
    return set(
        (
            await session.execute(
                select(RunColumn.name).where(
                    RunColumn.run_id == run_id,
                    RunColumn.org_id == org_id,
                    RunColumn.personal_data.is_not(None),
                )
            )
        ).scalars()
    )


def _check_decisions(
    ops: Sequence[CleaningOp], decisions: Mapping[str, bool], personal: set[str]
) -> None:
    """Decisions made so far (any subset) must name changes of this plan, and personal-data
    exclusions always stay."""
    by_id = {op.op_id: op for op in ops}
    if set(decisions) - set(by_id):
        raise InvalidDecision(
            "The decision names changes that are not in this cleaning plan.",
            hint="Reload the page and decide again.",
        )
    for op_id, accept in decisions.items():
        op = by_id[op_id]
        if (
            op.kind == CleaningKind.EXCLUDE_COLUMN.value
            and op.column_name in personal
            and not accept
        ):
            raise InvalidDecision(
                "Columns with personal data always stay out of the analysis.",
                hint="Keep the personal-data exclusions.",
            )


def _apply_decisions(
    ops: Sequence[CleaningOp], decisions: Mapping[str, bool], personal: set[str]
) -> list[dict[str, Any]]:
    by_id = {op.op_id: op for op in ops}
    unknown = set(decisions) - set(by_id)
    if unknown:
        raise InvalidDecision(
            "The decision names changes that are not in this cleaning plan.",
            hint="Reload the page and decide again.",
        )
    out = []
    for op in ops:
        data = _op_dict(op)
        if op.op_id not in decisions and op.decision == Decision.PENDING.value:
            # Approved without a decision: the change is not made (the data stays as it is).
            data["decision"] = Decision.REJECTED.value
            data["notice"] = "Kept as is: approved without a decision."
        elif op.op_id in decisions:
            accept = decisions[op.op_id]
            personal_exclusion = (
                op.kind == CleaningKind.EXCLUDE_COLUMN.value and op.column_name in personal
            )
            if personal_exclusion and not accept:
                raise InvalidDecision(
                    "Columns with personal data always stay out of the analysis.",
                    hint="Keep the personal-data exclusions.",
                )
            data["decision"] = (Decision.ACCEPTED if accept else Decision.REJECTED).value
            if data["decision"] != op.decision:
                data["notice"] = "Decided by a person."
        out.append(data)
    return out


def _hash(operations: Sequence[Mapping[str, Any]]) -> str:
    return content_hash(list(operations))


def _tidy(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join("".join(ch for ch in value if ch.isprintable()).split())
    return cleaned[:limit] or None
