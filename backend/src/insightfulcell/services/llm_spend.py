"""The gateway's ``SpendGuard`` over PostgreSQL: the spend caps and the spend ledger.

Every model attempt reserves its worst-case cost first (``app.llm_spend_reserve``, which
refuses when the overall cap, the provider's own cap or the person's has no room) and is
recorded with what it really cost when it ends (``app.llm_spend_record``; a trigger keeps each
provider's total). They are ``SECURITY DEFINER`` functions: the application role cannot read or
change the ledger directly.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text

from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.llm.gateway import CallRecord, SpendHold

logger = logging.getLogger(__name__)

#: Shares of the cap at which the log warns, once each as the total passes them.
WARN_AT: Final = (Decimal("0.5"), Decimal("0.8"), Decimal("0.95"))

_RESERVE = text(
    "SELECT hold_id, refused FROM app.llm_spend_reserve("
    ":org, :run, :provider, :amount, :cap, :user_cap, :provider_cap, :hold_seconds)"
)
_RECORD = text(
    "SELECT app.llm_spend_record(:hold, :org, :run, :label, :purpose, :provider, :model, "
    ":outcome, :fallback, :attempt, :input_tokens, :output_tokens, :cost, :estimated, "
    ":latency_ms)"
)
_BY_PROVIDER = text("SELECT app.llm_spend_by_provider()")


class DbSpendGuard:
    def __init__(  # noqa: PLR0913  (the cap's settings, all keyword-only)
        self,
        db: TenantDatabase,
        *,
        cap_usd: Decimal | None,
        user_cap_usd: Decimal,
        label: str,
        hold_seconds: int,
        usd_to_aud: Decimal,
        provider_caps_usd: Mapping[str, Decimal | None] | None = None,
    ) -> None:
        self._db = db
        self._cap = cap_usd  # None: no overall limit
        self._user_cap = user_cap_usd
        self._provider_caps = dict(provider_caps_usd or {})  # missing or None: no limit
        self._label = label
        self._hold_seconds = hold_seconds
        self._usd_to_aud = usd_to_aud

    async def reserve(
        self,
        org_id: UUID,
        run_id: UUID | None,
        provider: str,
        amount_usd: Decimal,
        input_usd: Decimal,
    ) -> SpendHold:
        async with self._db.unscoped_transaction() as session:
            row = (
                await session.execute(
                    _RESERVE,
                    {
                        "org": org_id,
                        "run": run_id,
                        "provider": provider,
                        "amount": amount_usd,
                        "cap": self._cap,
                        "user_cap": self._user_cap,
                        "provider_cap": self._provider_caps.get(provider),
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
            providers: list[dict[str, Any]] = list(
                (await session.execute(_BY_PROVIDER)).scalar_one()
            )
        provider_total = next(
            (Decimal(str(p["cost_usd"])) for p in providers if p["provider"] == call.provider),
            charged_usd,
        )
        self._log(Decimal(total), provider_total, charged_usd, call)

    def _log(
        self, total: Decimal, provider_total: Decimal, charged: Decimal, call: CallRecord
    ) -> None:
        """One structured line per attempt (cost, running totals), and a warning as a total
        passes each share of its cap. Never the prompt or the answer."""
        provider_cap = self._provider_caps.get(call.provider)
        logger.info(
            "model spend",
            extra={
                "provider": call.provider,
                "model": call.model,
                "outcome": call.outcome,
                "fallback": call.fallback,
                "cost_usd": str(charged),
                "total_usd": str(total),
                "total_aud": str(self._aud(total)),
                "cap_aud": self._cap_aud(self._cap),
                "provider_total_usd": str(provider_total),
                "provider_total_aud": str(self._aud(provider_total)),
                "provider_cap_aud": self._cap_aud(provider_cap),
                "label": self._label,
            },
        )
        self._warn("the cap", self._cap, total, charged)
        self._warn(f"the {call.provider} cap", provider_cap, provider_total, charged)

    def _warn(self, which: str, cap: Decimal | None, total: Decimal, charged: Decimal) -> None:
        if cap is None or cap <= 0:
            return
        before = total - charged
        for share in WARN_AT:
            line = cap * share
            if before < line <= total:
                logger.warning(
                    f"model spend passed a share of {which}",
                    extra={
                        "share": f"{share:.0%}",
                        "total_aud": str(self._aud(total)),
                        "cap_aud": self._cap_aud(cap),
                    },
                )

    def _aud(self, usd: Decimal) -> Decimal:
        return (usd * self._usd_to_aud).quantize(Decimal("0.0001"))

    def _cap_aud(self, cap: Decimal | None) -> str:
        """For the log: the cap in AUD, or ``none`` (no limit)."""
        return "none" if cap is None else str((cap * self._usd_to_aud).quantize(Decimal("0.01")))
