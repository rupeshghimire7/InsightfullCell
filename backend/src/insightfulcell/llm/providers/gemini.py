"""Google Gemini through the official ``google-genai`` SDK (the default provider, ADR-031).

Structured output: ``response_mime_type="application/json"`` with ``response_json_schema``.
The SDK's own retries are not used; the gateway retries with its own budget and breaker.

A 429 carries Google's ``RetryInfo`` (how long to wait) and ``QuotaFailure`` (which quota): a
per-minute limit is worth waiting for, a per-day quota is not (``quota_exhausted``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from insightfulcell.llm.transport import (
    PermanentProviderError,
    RawReply,
    TransientProviderError,
    parse_retry_after,
)

_RETRYABLE_CODES: Final = frozenset({408, 409, 429})
_AUTH_CODES: Final = frozenset({401, 403})
_RATE_LIMITED: Final = 429


def classify(exc: genai_errors.APIError) -> TransientProviderError | PermanentProviderError:
    """Our error for a Gemini API error; reads the 429's retry delay and quota kind."""
    code = int(exc.code or 0)
    if code == _RATE_LIMITED:
        retry_after, daily = _quota_details(exc.details)
        if daily:
            return PermanentProviderError("quota_exhausted", retry_after=retry_after)
        return TransientProviderError("rate_limited", retry_after=retry_after)
    if code in _RETRYABLE_CODES or code >= 500:
        return TransientProviderError("provider_error")
    if code in _AUTH_CODES:
        return PermanentProviderError("auth_failed")
    return PermanentProviderError("provider_error")


def _quota_details(details: object) -> tuple[float | None, bool]:
    """(seconds to wait, whether a per-day quota is used up) from an error body."""
    error = details.get("error", details) if isinstance(details, dict) else {}
    items = error.get("details", []) if isinstance(error, dict) else []
    retry_after: float | None = None
    daily = False
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("RetryInfo"):
            retry_after = parse_retry_after(item.get("retryDelay"))
        elif kind.endswith("QuotaFailure"):
            for violation in item.get("violations", []) or []:
                quota = str(violation.get("quotaId", "")) if isinstance(violation, dict) else ""
                daily = daily or "PerDay" in quota
    return retry_after, daily


class GeminiTransport:
    provider = "gemini"

    def __init__(self, api_key: str, *, timeout_seconds: float) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        max_output_tokens: int,
    ) -> RawReply:
        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=dict(schema),
                    max_output_tokens=max_output_tokens,
                ),
            )
        except genai_errors.APIError as exc:
            raise classify(exc) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise TransientProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError("provider_error") from exc

        candidates = response.candidates or []
        finish = (
            str(candidates[0].finish_reason) if candidates and candidates[0].finish_reason else ""
        )
        if "SAFETY" in finish or "PROHIBITED" in finish or "BLOCKLIST" in finish:
            raise PermanentProviderError("refused")
        usage = response.usage_metadata
        return RawReply(
            text=response.text or "",
            input_tokens=int(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(
                int(usage.candidates_token_count or 0) + int(usage.thoughts_token_count or 0)
                if usage
                else 0
            ),
            request_id=response.response_id,
            truncated="MAX_TOKENS" in finish,
        )

    async def check(self, model: str) -> None:
        """Looks the model up with the key (free: no tokens, no generation quota)."""
        try:
            await self._client.aio.models.get(model=model)
        except genai_errors.APIError as exc:
            error = classify(exc)
            if isinstance(error, PermanentProviderError) and error.reason == "provider_error":
                raise PermanentProviderError("model_not_found") from exc
            raise error from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise TransientProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError("provider_error") from exc
