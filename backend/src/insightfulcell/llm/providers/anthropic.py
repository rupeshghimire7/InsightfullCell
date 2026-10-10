"""Claude through the official ``anthropic`` SDK (Messages API).

Structured output: ``output_config.format`` with a JSON schema; the first text block is the
JSON. The stable system prompt is cached automatically (top-level ``cache_control``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import anthropic

from insightfulcell.llm.transport import PermanentProviderError, RawReply, TransientProviderError

_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
    anthropic.APIConnectionError,
)


class AnthropicTransport:
    provider = "anthropic"

    def __init__(self, api_key: str, *, timeout_seconds: float) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
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
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": dict(schema)}},
                cache_control={"type": "ephemeral"},
            )
        except anthropic.APITimeoutError as exc:
            raise TransientProviderError("timeout") from exc
        except _TRANSIENT as exc:
            reason = (
                "rate_limited" if isinstance(exc, anthropic.RateLimitError) else "provider_error"
            )
            raise TransientProviderError(reason) from exc
        except anthropic.APIStatusError as exc:
            raise PermanentProviderError("provider_error") from exc

        if response.stop_reason == "refusal":
            raise PermanentProviderError("refused")
        text = next((block.text for block in response.content if block.type == "text"), "")
        return RawReply(
            text=text,
            input_tokens=int(response.usage.input_tokens or 0)
            + int(response.usage.cache_read_input_tokens or 0)
            + int(response.usage.cache_creation_input_tokens or 0),
            output_tokens=int(response.usage.output_tokens or 0),
            request_id=getattr(response, "_request_id", None),
            truncated=response.stop_reason == "max_tokens",
        )
