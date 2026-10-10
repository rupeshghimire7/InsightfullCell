"""OpenAI and DeepSeek through the official ``openai`` SDK (Chat Completions).

OpenAI: strict structured output (``response_format`` ``json_schema`` with ``strict``).
DeepSeek: OpenAI-compatible endpoint with JSON mode (``json_object``); its JSON mode does not
take a schema, so the schema goes into the system prompt and our validation enforces it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import openai

from insightfulcell.llm.transport import PermanentProviderError, RawReply, TransientProviderError

SCHEMA_INSTRUCTION = (
    "\n\nRespond with one JSON object that validates against this JSON Schema. Every property "
    "is required; use null where a value does not apply.\n<json_schema>\n{schema}\n</json_schema>"
)


class OpenAICompatibleTransport:
    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        timeout_seconds: float,
        base_url: str | None = None,
        strict_schema: bool = True,
    ) -> None:
        self.provider = provider
        self._strict = strict_schema
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
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
        if self._strict:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": dict(schema)},
            }
            limits: dict[str, Any] = {"max_completion_tokens": max_output_tokens}
        else:
            response_format = {"type": "json_object"}
            system = system + SCHEMA_INSTRUCTION.format(schema=json.dumps(schema))
            limits = {"max_tokens": min(max_output_tokens, 8_192)}
        # Typed loosely at the SDK boundary: the request is built from plain data above.
        completions: Any = self._client.chat.completions
        try:
            response = await completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_format,
                **limits,
            )
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise TransientProviderError(
                "timeout" if isinstance(exc, openai.APITimeoutError) else "provider_error"
            ) from exc
        except openai.RateLimitError as exc:
            raise TransientProviderError("rate_limited") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in {408, 409}:
                raise TransientProviderError("provider_error") from exc
            raise PermanentProviderError("provider_error") from exc

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise TransientProviderError("provider_error")
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise PermanentProviderError("refused")
        usage = response.usage
        return RawReply(
            text=choice.message.content or "",
            input_tokens=int(usage.prompt_tokens) if usage else 0,
            output_tokens=int(usage.completion_tokens) if usage else 0,
            request_id=response.id,
            truncated=choice.finish_reason == "length",
        )
