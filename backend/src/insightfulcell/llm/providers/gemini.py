"""Google Gemini through the official ``google-genai`` SDK (the default provider, ADR-031).

Structured output: ``response_mime_type="application/json"`` with ``response_json_schema``.
The SDK's own retries are not used; the gateway retries with its own budget and breaker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from insightfulcell.llm.transport import PermanentProviderError, RawReply, TransientProviderError

_RETRYABLE_CODES: Final = frozenset({408, 409, 429})


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
            code = int(exc.code or 0)
            if code in _RETRYABLE_CODES or code >= 500:
                raise TransientProviderError(
                    "rate_limited" if code == 429 else "provider_error"
                ) from exc
            raise PermanentProviderError("provider_error") from exc
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
