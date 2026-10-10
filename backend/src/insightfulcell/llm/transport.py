"""The transport contract every provider adapter implements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RawReply:
    text: str
    input_tokens: int
    output_tokens: int
    request_id: str | None = None
    truncated: bool = False  # the provider stopped at the output limit


class TransientProviderError(Exception):
    """Worth retrying: rate limit, timeout, overload, a 5xx, a dropped connection."""

    def __init__(self, reason: str = "provider_error") -> None:
        super().__init__(reason)
        self.reason = reason  # timeout | rate_limited | provider_error


class PermanentProviderError(Exception):
    """Retrying will not help: bad key, unknown model, rejected request, a refusal."""

    def __init__(self, reason: str = "provider_error") -> None:
        super().__init__(reason)
        self.reason = reason  # refused | provider_error


class Transport(Protocol):
    provider: str

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        max_output_tokens: int,
    ) -> RawReply: ...
