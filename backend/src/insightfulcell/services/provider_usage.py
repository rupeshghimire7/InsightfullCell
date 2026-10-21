"""What OpenAI itself reports about usage and cost (its organisation Usage and Costs APIs).

Needs an OpenAI *admin* key (``OPENAI_ADMIN_KEY``, read-only), which is separate from the key
the model calls use. Answers are cached for a few minutes and fetched at most once at a time;
any failure is reported as unavailable, never raised, so the dashboard still opens. Gemini has
no such API for AI Studio keys: its usage is what our own ledger counts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

OPENAI_API: Final = "https://api.openai.com/v1/organization"
CACHE_SECONDS: Final = 600.0
TIMEOUT_SECONDS: Final = 10.0
MAX_PAGES: Final = 10
DAY: Final = 86_400


class OpenAIUsage:
    def __init__(
        self,
        admin_key: str | None,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._key = admin_key
        self._client = client
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached: tuple[float, int, dict[str, Any]] | None = None

    async def report(self, days: int) -> dict[str, Any]:
        if not self._key:
            return {"status": "not_configured"}
        async with self._lock:
            now = self._clock()
            if self._cached and self._cached[1] == days and now - self._cached[0] < CACHE_SECONDS:
                return self._cached[2]
            report = await self._fetch(days, now)
            self._cached = (now, days, report)
            return report

    async def _fetch(self, days: int, now: float) -> dict[str, Any]:
        start = int(now) - days * DAY
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            costs = await self._pages(client, "costs", {"start_time": start, "bucket_width": "1d"})
            usage = await self._pages(
                client,
                "usage/completions",
                {"start_time": start, "bucket_width": "1d", "group_by": "model"},
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            # Never the key, the URL's query or the body: only what kind of failure it was.
            logger.warning(
                "could not read OpenAI's usage report",
                extra={"error": type(exc).__name__, "status": status},
            )
            return {"status": "unavailable", "http_status": status}
        finally:
            if self._client is None:
                await client.aclose()
        return {"status": "ok", "fetched_at": int(now), **_summarise(costs, usage)}

    async def _pages(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        buckets: list[dict[str, Any]] = []
        page: str | None = None
        for _ in range(MAX_PAGES):
            response = await client.get(
                f"{OPENAI_API}/{path}",
                params={**params, "limit": 31, **({"page": page} if page else {})},
                headers={"Authorization": f"Bearer {self._key}"},
            )
            response.raise_for_status()
            body = response.json()
            buckets.extend(body.get("data") or [])
            page = body.get("next_page")
            if not body.get("has_more") or not page:
                break
        return buckets


def _summarise(costs: list[dict[str, Any]], usage: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: list[dict[str, Any]] = []
    total = Decimal(0)
    currency = "usd"
    for bucket in costs:
        amount = Decimal(0)
        for result in bucket.get("results") or []:
            value = (result.get("amount") or {}).get("value") or 0
            currency = str((result.get("amount") or {}).get("currency") or currency)
            amount += Decimal(str(value))
        total += amount
        by_day.append({"start_time": int(bucket["start_time"]), "cost": str(amount)})
    models: dict[str, dict[str, int]] = {}
    for bucket in usage:
        for result in bucket.get("results") or []:
            row = models.setdefault(
                str(result.get("model") or "unknown"),
                {"input_tokens": 0, "output_tokens": 0, "requests": 0},
            )
            row["input_tokens"] += int(result.get("input_tokens") or 0)
            row["output_tokens"] += int(result.get("output_tokens") or 0)
            row["requests"] += int(result.get("num_model_requests") or 0)
    return {
        "currency": currency,
        "total_cost": str(total),
        "cost_by_day": by_day,
        "by_model": [{"model": name, **row} for name, row in sorted(models.items())],
    }
