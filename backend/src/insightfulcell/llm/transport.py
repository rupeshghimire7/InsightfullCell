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
    """Worth retrying: rate limit, timeout, overload, a 5xx, a dropped connection.

    ``retry_after`` is the wait the provider asked for (seconds), when it said.
    """

    def __init__(self, reason: str = "provider_error", *, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason  # timeout | rate_limited | provider_error
        self.retry_after = retry_after


class PermanentProviderError(Exception):
    """Retrying will not help: bad key, unknown model, rejected request, a refusal, a quota
    used up for the day. ``retry_after`` (seconds) says when a used-up quota comes back."""

    def __init__(self, reason: str = "provider_error", *, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason  # refused | provider_error | auth_failed | quota_exhausted
        self.retry_after = retry_after


def parse_retry_after(value: object) -> float | None:
    """Seconds from a ``Retry-After`` header (seconds only) or a Google ``retryDelay`` such as
    ``"35s"`` or ``"1.5s"``; ``None`` when absent or unreadable."""
    if value is None:
        return None
    text = str(value).strip().removesuffix("s")
    try:
        seconds = float(text)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


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

    async def check(self, model: str) -> None:
        """Prove the key works and the model exists, without generating anything (free).
        Raises ``PermanentProviderError`` (``auth_failed`` or ``provider_error``) when not."""
        ...
