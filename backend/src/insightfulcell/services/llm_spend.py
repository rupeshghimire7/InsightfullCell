"""The gateway's ``SpendGuard`` over PostgreSQL: the spend cap and the spend ledger.

Every model attempt reserves its worst-case cost first (``app.llm_spend_reserve``, which
refuses when the cap has no room) and is recorded with what it really cost when it ends
(``app.llm_spend_record``). Both are ``SECURITY DEFINER`` functions: the application role
cannot read or change the ledger directly.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy import text

from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.llm.gateway import CallRecord, SpendHold

logger = logging.getLogger(__name__)

#: Shares of the cap at which the log warns, once each as the total passes them.
WARN_AT: Final = (Decimal("0.5"), Decimal("0.8"), Decimal("0.95"))

_RESERVE = text(
    "SELECT hold_id, refused FROM app.llm_spend_reserve("
    ":org, :run, :amount, :cap, :user_cap, :hold_seconds)"
)
_RECORD = text(
    "SELECT app.llm_spend_record(:hold, :org, :run, :label, :purpose, :provider, :model, "
    ":outcome, :fallback, :attempt, :input_tokens, :output_tokens, :cost, :estimated, "
    ":latency_ms)"
)


class DbSpendGuard:
    def __init__(  # noqa: PLR0913  (the cap's settings, all keyword-only)
        self,
        db: TenantDatabase,
        *,
        cap_usd: Decimal,
        user_cap_usd: Decimal,
        label: str,
        hold_seconds: int,
        usd_to_aud: Decimal,
    ) -> None:
        self._db = db
        self._cap = cap_usd
        self._user_cap = user_cap_usd
        self._label = label
        self._hold_seconds = hold_seconds
        self._usd_to_aud = usd_to_aud

    async def reserve(
        self, org_id: UUID, run_id: UUID | None, amount_usd: Decimal, input_usd: Decimal
    ) -> SpendHold:
        async with self._db.unscoped_transaction() as session:
            row = (
                await session.execute(
                    _RESERVE,
                    {
                        "org": org_id,
                        "run": run_id,
                        "amount": amount_usd,
                        "cap": self._cap,
                        "user_cap": self._user_cap,
                        "hold_seconds": self._hold_seconds,
                    },
                )
            ).one()
        return SpendHold(
            id=row.hold_id, amount_usd=amount_usd, input_usd=input_usd, refused=row.refused
        )

    async def settle(
        self, hold: SpendHold, call: CallRecord, charged_usd: Decimal, *, estimated: bool
    ) -> None:
        async with self._db.unscoped_transaction() as session:
            total = (
                await session.execute(
                    _RECORD,
                    {
                        "hold": hold.id,
                        "org": call.org_id,
                        "run": call.run_id,
                        "label": self._label,
                        "purpose": call.purpose,
                        "provider": call.provider,
                        "model": call.model,
                        "outcome": call.outcome,
                        "fallback": call.fallback,
                        "attempt": call.attempt,
                        "input_tokens": call.input_tokens,
                        "output_tokens": call.output_tokens,
                        "cost": charged_usd,
                        "estimated": estimated,
                        "latency_ms": call.latency_ms,
                    },
                )
            ).scalar_one()
        self._log(Decimal(total), charged_usd, call)

    def _log(self, total: Decimal, charged: Decimal, call: CallRecord) -> None:
        """One structured line per attempt (cost and running total), and a warning as the total
        passes each share of the cap. Never the prompt or the answer."""
        aud = (total * self._usd_to_aud).quantize(Decimal("0.0001"))
        cap_aud = (self._cap * self._usd_to_aud).quantize(Decimal("0.01"))
        logger.info(
            "model spend",
            extra={
                "provider": call.provider,
                "model": call.model,
                "outcome": call.outcome,
                "fallback": call.fallback,
                "cost_usd": str(charged),
                "total_usd": str(total),
                "total_aud": str(aud),
                "cap_aud": str(cap_aud),
                "label": self._label,
            },
        )
        if self._cap <= 0:
            return
        before = total - charged
        for share in WARN_AT:
            line = self._cap * share
            if before < line <= total:
                logger.warning(
                    "model spend passed a share of the cap",
                    extra={"share": f"{share:.0%}", "total_aud": str(aud), "cap_aud": str(cap_aud)},
                )
