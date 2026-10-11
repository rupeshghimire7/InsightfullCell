"""The ``AnalysisRepository`` port over PostgreSQL: a run's columns, plans, cleaning plans,
facts and report.

Every read and write happens in a transaction scoped to the run's organisation (row-level
security). Plans, cleaning plans, facts and reports are append-only: a new revision, never an
update.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from insightfulcell.db.models import (
    AnalysisPlan,
    CleaningOp,
    CleaningPlan,
    RunColumn,
    RunFact,
    RunReport,
    TestLog,
    VerificationResult,
)
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.engine.cleaning.operations import CATALOGUE
from insightfulcell.engine.planning.language import CleaningKind
from insightfulcell.engine.ports import (
    CachedPlan,
    CleaningRecord,
    ColumnRecord,
    PlanRecord,
    ReportRecord,
    TestRecord,
)


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

    async def latest_resolved_plan(self) -> Mapping[str, Any] | None:
        async with self._db.system_transaction(self._org_id) as session:
            plan: Mapping[str, Any] | None = (
                await session.execute(
                    select(AnalysisPlan.plan)
                    .where(
                        AnalysisPlan.run_id == self._run_id,
                        AnalysisPlan.org_id == self._org_id,
                        AnalysisPlan.kind == "resolved",
                    )
                    .order_by(AnalysisPlan.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return plan

    async def approved_cleaning(self) -> Sequence[Mapping[str, Any]] | None:
        async with self._db.system_transaction(self._org_id) as session:
            plan = (
                await session.execute(
                    select(CleaningPlan)
                    .where(
                        CleaningPlan.run_id == self._run_id,
                        CleaningPlan.org_id == self._org_id,
                    )
                    .order_by(CleaningPlan.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if plan is None or plan.status != "approved":
                return None
            ops = (
                await session.execute(
                    select(CleaningOp)
                    .where(CleaningOp.plan_id == plan.id, CleaningOp.org_id == self._org_id)
                    .order_by(CleaningOp.position)
                )
            ).scalars()
            return [
                {
                    "id": op.op_id,
                    "kind": op.kind,
                    "column": op.column_name,
                    "params": op.params,
                    "lossy": op.lossy,
                    "default": op.default_mode,
                    "decision": op.decision,
                    "reason": op.reason,
                    "source": op.source,
                    "order": CATALOGUE[CleaningKind(op.kind)].order,
                    "rows_affected": op.rows_affected,
                    "values_lost": op.values_lost,
                    "preview": op.preview,
                    "notice": op.notice,
                }
                for op in ops
            ]

    async def save_findings(
        self, facts: Sequence[Mapping[str, Any]], tests: Sequence[TestRecord]
    ) -> None:
        """Facts and their tests in one transaction. A retried stage computes the same facts
        (mining is deterministic): then nothing is written again. Different facts under the
        same ids would make the report cite something other than what is stored, so they are
        refused."""
        rows = [self._fact_row(f) for f in facts]
        async with self._db.system_transaction(self._org_id) as session:
            result = await session.execute(
                select(RunFact.fact_id, RunFact.content_hash).where(
                    RunFact.run_id == self._run_id, RunFact.org_id == self._org_id
                )
            )
            stored = dict(result.all())
            if stored:
                if stored != {r["fact_id"]: r["content_hash"] for r in rows}:
                    raise FactsChanged(
                        "this run already stored different facts; facts are immutable"
                    )
                return
            if rows:
                await session.execute(insert(RunFact).values(rows))
            if tests:
                await session.execute(insert(TestLog).values([self._test_row(t) for t in tests]))

    async def save_rechecks(self, tests: Sequence[TestRecord]) -> None:
        async with self._db.system_transaction(self._org_id) as session:
            done = (
                await session.execute(
                    select(func.count())
                    .select_from(TestLog)
                    .where(
                        TestLog.run_id == self._run_id,
                        TestLog.org_id == self._org_id,
                        TestLog.purpose == "sensitivity",
                    )
                )
            ).scalar_one()
            if done or not tests:
                return
            await session.execute(insert(TestLog).values([self._test_row(t) for t in tests]))

    def _fact_row(self, f: Mapping[str, Any]) -> dict[str, Any]:
        test = f.get("test") or {}
        return {
            "org_id": self._org_id,
            "run_id": self._run_id,
            "fact_id": f["id"],
            "analysis_id": f["analysis_id"],
            "kind": f["kind"],
            "columns": list(f["columns"]),
            "rows": int(f["rows"]),
            "method": str(f["method"])[:2000],
            "statement": str(f["statement"])[:4000],
            "numbers": list(f.get("numbers") or []),
            "labels": list(f.get("labels") or []),
            "value": dict(f.get("values") or {}),
            "content_hash": f["content_hash"],
            "tier": f.get("tier", "descriptive"),
            "hypothesis": test.get("hypothesis"),
            "test_method": test.get("method"),
            "effect_name": test.get("effect_name"),
            "effect": test.get("effect"),
            "ci_low": test.get("ci_low"),
            "ci_high": test.get("ci_high"),
            "p_value": test.get("p_value"),
            "q_value": test.get("q_value"),
            "provenance": dict(f.get("provenance") or {}),
        }

    def _test_row(self, t: TestRecord) -> dict[str, Any]:
        return {
            "id": new_uuid7(),
            "org_id": self._org_id,
            "run_id": self._run_id,
            "purpose": t.purpose,
            "hypothesis": t.hypothesis[:4000],
            "method": t.method[:500],
            "columns": list(t.columns),
            "n": int(t.n),
            "effect_name": t.effect_name,
            "effect": _finite(t.effect),
            "ci_low": _finite(t.ci_low),
            "ci_high": _finite(t.ci_high),
            "p_value": _finite(t.p_value),
            "q_value": _finite(t.q_value),
            "alpha": t.alpha,
            "passed": t.passed,
            "outcome": t.outcome,
            "fact_id": t.fact_id,
        }

    async def save_report(self, record: ReportRecord) -> None:
        async with self._db.system_transaction(self._org_id) as session:
            revision = await _next_revision(session, RunReport, RunReport.run_id == self._run_id)
            session.add(
                RunReport(
                    id=new_uuid7(),
                    org_id=self._org_id,
                    run_id=self._run_id,
                    revision=revision,
                    document=dict(record.document),
                    narration_source=record.narration_source,
                    provider=record.provider,
                    model=record.model,
                    prompt_version=record.prompt_version,
                    dashboard=dict(record.dashboard) if record.dashboard is not None else None,
                )
            )
            if record.verification:
                await session.execute(
                    insert(VerificationResult).values(
                        [
                            {
                                "id": new_uuid7(),
                                "org_id": self._org_id,
                                "run_id": self._run_id,
                                "report_revision": revision,
                                "part": v.part[:4000],
                                "position": v.position,
                                "attempt": v.attempt,
                                "accepted": v.accepted,
                                "check": v.check,
                                "detail": (v.detail or "")[:500] or None,
                                "citations": list(v.citations),
                                "sentence": v.text[:2000],
                                "prompt_version": record.prompt_version or "unknown",
                            }
                            for v in record.verification
                        ]
                    )
                )


class FactsChanged(RuntimeError):
    """A retry computed different facts than the run already stored (never expected: mining
    is deterministic)."""


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


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
