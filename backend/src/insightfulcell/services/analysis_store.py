"""The ``AnalysisRepository`` port over PostgreSQL: a run's columns, plans and cleaning plans.

Every write happens in a transaction scoped to the run's organisation (row-level security).
Plans and cleaning plans are append-only: a new revision, never an update.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select

from insightfulcell.db.models import AnalysisPlan, CleaningOp, CleaningPlan, RunColumn
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.engine.ports import CachedPlan, CleaningRecord, ColumnRecord, PlanRecord


class RunAnalysisStore:
    def __init__(self, db: TenantDatabase, *, org_id: UUID, run_id: UUID, dataset_id: UUID) -> None:
        self._db = db
        self._org_id = org_id
        self._run_id = run_id
        self._dataset_id = dataset_id

    async def save_columns(self, columns: Sequence[ColumnRecord]) -> None:
        async with self._db.system_transaction(self._org_id) as session:
            await session.execute(
                delete(RunColumn).where(
                    RunColumn.run_id == self._run_id, RunColumn.org_id == self._org_id
                )
            )
            session.add_all(
                RunColumn(
                    org_id=self._org_id,
                    run_id=self._run_id,
                    position=c.position,
                    name=c.name,
                    physical_type=c.physical_type,
                    meaning=c.meaning,
                    confidence=c.confidence,
                    personal_data=c.personal_data,
                    unit=c.unit,
                    display_name=c.display_name,
                    role=c.role,
                    flags=list(c.flags),
                    profile=dict(c.profile),
                )
                for c in columns
            )

    async def find_cached_plan(
        self, schema_fingerprint: str, prompt_version: str
    ) -> CachedPlan | None:
        async with self._db.system_transaction(self._org_id) as session:
            row = (
                await session.execute(
                    select(AnalysisPlan)
                    .where(
                        AnalysisPlan.org_id == self._org_id,
                        AnalysisPlan.kind == "proposal",
                        AnalysisPlan.source == "model",
                        AnalysisPlan.schema_fingerprint == schema_fingerprint,
                        AnalysisPlan.prompt_version == prompt_version,
                    )
                    .order_by(AnalysisPlan.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return CachedPlan(plan=row.plan, provider=row.provider, model=row.model)

    async def save_plan(self, record: PlanRecord) -> None:
        async with self._db.system_transaction(self._org_id) as session:
            revision = await _next_revision(
                session,
                AnalysisPlan,
                AnalysisPlan.run_id == self._run_id,
                AnalysisPlan.kind == record.kind,
            )
            session.add(
                AnalysisPlan(
                    id=new_uuid7(),
                    org_id=self._org_id,
                    run_id=self._run_id,
                    dataset_id=self._dataset_id,
                    kind=record.kind,
                    revision=revision,
                    source=record.source,
                    schema_fingerprint=record.schema_fingerprint,
                    prompt_version=record.prompt_version,
                    provider=record.provider,
                    model=record.model,
                    plan=dict(record.plan),
                    plan_hash=record.plan_hash,
                    rejected=[dict(r) for r in record.rejected],
                    input_card=dict(record.input_card) if record.input_card is not None else None,
                    fallback_reason=record.fallback_reason,
                )
            )

    async def save_cleaning_plan(self, record: CleaningRecord) -> None:
        async with self._db.system_transaction(self._org_id) as session:
            await insert_cleaning_plan(
                session,
                org_id=self._org_id,
                run_id=self._run_id,
                policy=record.policy,
                status=record.status,
                plan_hash=record.plan_hash,
                operations=record.operations,
            )


async def _next_revision(session: Any, model: Any, *conditions: Any) -> int:
    current = (
        await session.execute(select(func.coalesce(func.max(model.revision), 0)).where(*conditions))
    ).scalar_one()
    return int(current) + 1


async def insert_cleaning_plan(  # noqa: PLR0913  (keyword-only)
    session: Any,
    *,
    org_id: UUID,
    run_id: UUID,
    policy: str,
    status: str,
    plan_hash: str,
    operations: Sequence[Mapping[str, Any]],
    decided_by: UUID | None = None,
) -> CleaningPlan:
    """Append a cleaning plan revision with its operations (shared with the review decision)."""
    revision = await _next_revision(session, CleaningPlan, CleaningPlan.run_id == run_id)
    plan = CleaningPlan(
        id=new_uuid7(),
        org_id=org_id,
        run_id=run_id,
        revision=revision,
        policy=policy,
        status=status,
        decided_by=decided_by,
        decided_at=func.now() if decided_by is not None else None,
        plan_hash=plan_hash,
    )
    session.add(plan)
    await session.flush()
    session.add_all(
        CleaningOp(
            org_id=org_id,
            plan_id=plan.id,
            position=position,
            op_id=op["id"],
            kind=op["kind"],
            column_name=op.get("column"),
            params=dict(op.get("params") or {}),
            lossy=bool(op["lossy"]),
            default_mode=op["default"],
            decision=op["decision"],
            reason=str(op.get("reason") or "")[:2000],
            source=op["source"],
            rows_affected=int(op.get("rows_affected") or 0),
            values_lost=int(op.get("values_lost") or 0),
            preview=list(op.get("preview") or []),
            notice=op.get("notice"),
        )
        for position, op in enumerate(operations)
    )
    await session.flush()
    return plan
