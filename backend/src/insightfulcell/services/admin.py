"""The admin dashboard's figures: everyone's usage, from ``app.admin_overview``.

Counts and sums only (people by id, never file names, rows, prompts or keys). The web app adds
names and emails from its own sign-in tables, which this service never reads.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from typing import Any

from pydantic import SecretStr
from sqlalchemy import text

from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.services.provider_usage import OpenAIUsage

_OVERVIEW = text("SELECT app.admin_overview(:days)")
_BY_PROVIDER = text("SELECT app.llm_spend_by_provider()")


def admin_key_matches(supplied: str | None, configured: SecretStr | None) -> bool:
    """Constant-time comparison (of digests, so the length does not leak either)."""
    if configured is None or not supplied:
        return False
    return hmac.compare_digest(
        sha256(supplied.encode()).digest(),
        sha256(configured.get_secret_value().encode()).digest(),
    )


class AdminService:
    def __init__(
        self,
        db: TenantDatabase,
        settings: Settings,
        openai: OpenAIUsage,
        models: Mapping[str, str],
    ) -> None:
        self._db = db
        self._settings = settings
        self._openai = openai
        self._models = dict(models)  # the main provider's models, defaults filled in

    async def overview(self, days: int) -> dict[str, Any]:
        async with self._db.unscoped_transaction() as session:
            data: dict[str, Any] = dict(
                (await session.execute(_OVERVIEW, {"days": days})).scalar_one()
            )
            by_provider = (await session.execute(_BY_PROVIDER)).scalar_one()
        data["spend"] = {**data["spend"], "by_provider": self._by_provider(by_provider)}
        data["config"] = self._config()
        data["provider_reported"] = {"openai": await self._openai.report(days)}
        return data

    def _by_provider(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each provider's lifetime spend beside its own cap (``None``: no limit), including
        a configured provider that has not spent anything yet."""
        s = self._settings
        caps = s.provider_spend_caps_aud
        seen = {row["provider"] for row in rows}
        configured = [
            p
            for p in (s.llm_provider, s.llm_fallback_provider)
            if p != "none" and s.provider_key(p) is not None and p not in seen
        ]
        rows = [
            *rows,
            *({"provider": p, "cost_usd": 0, "calls": 0, "held_usd": 0} for p in configured),
        ]
        return [{**row, "cap_aud": _text(caps.get(row["provider"]))} for row in rows]

    def _config(self) -> dict[str, Any]:
        """How the model and the cap are set up. Never a key."""
        s = self._settings
        return {
            "environment": s.environment,
            "label": s.llm_usage_label,
            "provider": s.llm_provider if s.llm_api_key else "none",
            "models": self._models,
            "fallback_provider": s.llm_fallback_provider,
            "fallback_model": s.llm_fallback_model,
            "spend_cap_aud": _text(s.llm_spend_cap_aud),
            "spend_cap_usd": _text(s.llm_spend_cap_usd),
            "provider_spend_caps_aud": {
                p: _text(cap) for p, cap in s.provider_spend_caps_aud.items()
            },
            "user_spend_cap_aud": str(s.llm_user_spend_cap_aud),
            "usd_to_aud": str(s.llm_usd_to_aud),
            "requests_per_minute": s.llm_requests_per_minute,
            "max_output_tokens": s.llm_max_output_tokens,
            "worker_concurrency": s.worker_concurrency,
            "openai_admin_key_set": s.openai_admin_key is not None,
        }


def _text(value: Decimal | None) -> str | None:
    """A cap for the dashboard: its amount, or ``None`` for no limit."""
    return None if value is None else str(value)
