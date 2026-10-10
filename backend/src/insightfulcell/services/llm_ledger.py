"""The gateway's ``UsageLedger`` over PostgreSQL: ``llm_calls`` rows and monthly totals."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, cast, func, select, text

from insightfulcell.db.models import LlmCall, Organization, UsageCounter
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.llm.gateway import CallRecord

_BUMP = text(
    """
    INSERT INTO app.usage_counters (org_id, month, input_tokens, output_tokens, cost_usd, calls)
    VALUES (:org_id, date_trunc('month', now())::date, :input, :output, :cost, 1)
    ON CONFLICT (org_id, month) DO UPDATE SET
        input_tokens = app.usage_counters.input_tokens + EXCLUDED.input_tokens,
        output_tokens = app.usage_counters.output_tokens + EXCLUDED.output_tokens,
        cost_usd = app.usage_counters.cost_usd + EXCLUDED.cost_usd,
        calls = app.usage_counters.calls + 1
    """
)


class DbUsageLedger:
    def __init__(self, db: TenantDatabase) -> None:
        self._db = db

    async def run_input_tokens(self, org_id: UUID, run_id: UUID) -> int:
        async with self._db.system_transaction(org_id) as session:
            used = (
                await session.execute(
                    select(func.coalesce(func.sum(LlmCall.input_tokens), 0)).where(
                        LlmCall.org_id == org_id, LlmCall.run_id == run_id
                    )
                )
            ).scalar_one()
        return int(used)

    async def month_usage(self, org_id: UUID) -> tuple[int, int]:
        async with self._db.system_transaction(org_id) as session:
            used = (
                await session.execute(
                    select(UsageCounter.input_tokens + UsageCounter.output_tokens).where(
                        UsageCounter.org_id == org_id,
                        UsageCounter.month == cast(func.date_trunc("month", func.now()), Date),
                    )
                )
            ).scalar_one_or_none()
            budget = (
                await session.execute(
                    select(Organization.monthly_token_budget).where(Organization.id == org_id)
                )
            ).scalar_one_or_none()
        return int(used or 0), int(budget if budget is not None else 0)

    async def record(self, call: CallRecord) -> None:
        async with self._db.system_transaction(call.org_id) as session:
            session.add(
                LlmCall(
                    id=new_uuid7(),
                    org_id=call.org_id,
                    run_id=call.run_id,
                    purpose=call.purpose,
                    provider=call.provider,
                    model=call.model,
                    prompt_id=call.prompt_id,
                    prompt_version=call.prompt_version,
                    attempt=call.attempt,
                    outcome=call.outcome,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost_usd=call.cost_usd,
                    latency_ms=call.latency_ms,
                    provider_request_id=call.provider_request_id,
                )
            )
            if call.input_tokens or call.output_tokens:
                await session.execute(
                    _BUMP,
                    {
                        "org_id": call.org_id,
                        "input": call.input_tokens,
                        "output": call.output_tokens,
                        "cost": call.cost_usd or Decimal(0),
                    },
                )
