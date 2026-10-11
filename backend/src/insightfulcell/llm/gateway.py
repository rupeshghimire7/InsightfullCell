"""The LLM gateway (system map, AI harness): cost, privacy and reliability rules, written once.

Per call:

1. **Purpose -> model.** Planning, narration and the agent use the strong tier; routing and the
   judge use the fast tier.
2. **Breaker.** If the provider has been failing, fail fast.
3. **Payload check.** A value from a personal-data column, an email address, a US social
   security number or a payment card number anywhere in the prompt blocks the call and logs a
   security event (never the value).
4. **Budgets.** Per run (input tokens) and per organisation per month (all tokens).
5. **Call** with the provider's structured output, a timeout, and up to ``max_attempts``
   attempts with exponential backoff for transient errors.
6. **Validate** the answer with Pydantic on our side; an invalid answer is another attempt.
7. **Account** every attempt in ``llm_calls`` (purpose, model, prompt version, tokens, cost,
   latency, outcome). No prompt text is stored.

Any failure raises ``ModelUnavailable``: the caller falls back to its deterministic path, so a
model problem never fails a run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from insightfulcell.engine.ports import ModelReply, ModelRequest, ModelUnavailable, Purpose
from insightfulcell.llm.breaker import CircuitBreaker
from insightfulcell.llm.pricing import cost_usd
from insightfulcell.llm.transport import (
    PermanentProviderError,
    RawReply,
    TransientProviderError,
    Transport,
)

logger = logging.getLogger(__name__)
security_log = logging.getLogger("insightfulcell.security")

TIER: Final[dict[Purpose, str]] = {
    Purpose.PLAN: "strong",
    Purpose.NARRATE: "strong",
    Purpose.AGENT: "strong",
    Purpose.ROUTE: "fast",
    Purpose.JUDGE: "fast",
}
CHARS_PER_TOKEN: Final = 3.4
MIN_SENSITIVE_CHARS: Final = 4
_EMAIL: Final = re.compile(r"[^\s@\"'<>]+@[^\s@\"'<>]+\.[A-Za-z]{2,}")
_SSN: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD: Final = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_FENCE: Final = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


@dataclass(frozen=True, slots=True)
class CallRecord:
    org_id: UUID
    run_id: UUID | None
    purpose: str
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    attempt: int
    outcome: str  # ok, invalid, timeout, rate_limited, provider_error, refused, blocked
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    provider_request_id: str | None = None


class UsageLedger(Protocol):
    async def run_input_tokens(self, org_id: UUID, run_id: UUID) -> int: ...

    async def month_usage(self, org_id: UUID) -> tuple[int, int]:
        """(tokens used this month, the organisation's monthly budget)."""
        ...

    async def record(self, call: CallRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    models: Mapping[str, str]  # tier -> model id
    max_attempts: int = 3
    run_input_token_budget: int = 120_000
    max_output_tokens: int = 16_000
    prices: Mapping[str, tuple[float, float]] | None = None
    backoff_base_seconds: float = 1.0


def payload_blocked(text: str, sensitive: frozenset[str]) -> str | None:
    """Why the payload must not leave, or ``None``. Never returns the offending value."""
    folded = text.casefold()
    for value in sensitive:
        if len(value) >= MIN_SENSITIVE_CHARS and value.casefold() in folded:
            return "personal_data_value"
    if _EMAIL.search(text):
        return "email_address"
    if _SSN.search(text):
        return "national_id"
    for match in _CARD.finditer(text):
        if _luhn("".join(ch for ch in match.group(0) if ch.isdigit())):
            return "payment_card"
    return None


def _luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def estimate_tokens(*texts: str) -> int:
    return math.ceil(sum(len(t) for t in texts) / CHARS_PER_TOKEN)


class LLMGateway:
    def __init__(  # noqa: PLR0913  (collaborators; tests inject sleep and clock)
        self,
        transport: Transport | None,
        ledger: UsageLedger,
        config: GatewayConfig,
        breaker: CircuitBreaker,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._ledger = ledger
        self._config = config
        self._breaker = breaker
        self._sleep = sleep
        self._clock = clock

    @property
    def identity(self) -> tuple[str, str] | None:
        if self._transport is None:
            return None
        return self._transport.provider, self._config.models["strong"]

    async def complete(self, request: ModelRequest) -> ModelReply:
        transport = self._transport
        if transport is None:
            raise ModelUnavailable("disabled", "No language model is configured.")
        model = self._config.models[TIER[request.purpose]]
        if not self._breaker.allow():
            raise ModelUnavailable("breaker_open", "The model provider has been failing.")

        blocked = payload_blocked(request.system + "\n" + request.user, request.sensitive_values)
        if blocked is not None:
            security_log.warning(
                "model call blocked: payload holds personal data",
                extra={
                    "org_id": str(request.org_id),
                    "run_id": str(request.run_id) if request.run_id else None,
                    "purpose": request.purpose.value,
                    "check": blocked,
                },
            )
            await self._record(request, transport.provider, model, 0, "blocked", None, 0.0)
            raise ModelUnavailable("payload_blocked", "The request held personal data.")

        estimate = estimate_tokens(request.system, request.user)
        if request.run_id is not None:
            used = await self._ledger.run_input_tokens(request.org_id, request.run_id)
            if used + estimate > self._config.run_input_token_budget:
                raise ModelUnavailable("budget_run", "This run's model budget is spent.")
        month_used, month_budget = await self._ledger.month_usage(request.org_id)
        if month_used + estimate > month_budget:
            raise ModelUnavailable(
                "budget_org", "The organisation's monthly model budget is spent."
            )

        reason = "provider_error"
        for attempt in range(1, self._config.max_attempts + 1):
            started = self._clock()
            try:
                raw = await transport.generate(
                    model=model,
                    system=request.system,
                    user=request.user,
                    schema=request.response_schema,
                    max_output_tokens=self._config.max_output_tokens,
                )
            except TransientProviderError as exc:
                reason = exc.reason
                self._breaker.failure()
                await self._record(
                    request, transport.provider, model, attempt, reason, None, started
                )
                if attempt < self._config.max_attempts:
                    await self._sleep(self._backoff(attempt))
                continue
            except PermanentProviderError as exc:
                self._breaker.failure()
                await self._record(
                    request, transport.provider, model, attempt, exc.reason, None, started
                )
                raise ModelUnavailable(
                    exc.reason, "The model provider refused the request."
                ) from exc

            parsed, cause = _parse(raw, request)
            if parsed is None:
                reason = "invalid_output"
                # Why, without any of the answer's content (it may echo the prompt).
                logger.warning(
                    "model answer unusable",
                    extra={
                        "purpose": request.purpose.value,
                        "cause": cause,
                        "attempt": attempt,
                        "output_tokens": raw.output_tokens,
                    },
                )
                await self._record(
                    request, transport.provider, model, attempt, "invalid", raw, started
                )
                continue
            self._breaker.success()
            await self._record(request, transport.provider, model, attempt, "ok", raw, started)
            return ModelReply(
                data=parsed,
                provider=transport.provider,
                model=model,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                attempts=attempt,
            )
        raise ModelUnavailable(reason, "No usable answer from the model.")

    def _backoff(self, attempt: int) -> float:
        base = self._config.backoff_base_seconds * float(2 ** (attempt - 1))
        return base + random.uniform(0, base / 2)  # noqa: S311  (jitter, not security)

    async def _record(  # noqa: PLR0913, PLR0917
        self,
        request: ModelRequest,
        provider: str,
        model: str,
        attempt: int,
        outcome: str,
        raw: RawReply | None,
        started: float,
    ) -> None:
        latency = round((self._clock() - started) * 1000) if started else 0
        input_tokens = raw.input_tokens if raw else 0
        output_tokens = raw.output_tokens if raw else 0
        call = CallRecord(
            org_id=request.org_id,
            run_id=request.run_id,
            purpose=request.purpose.value,
            provider=provider,
            model=model,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            attempt=attempt,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens, self._config.prices),
            latency_ms=max(0, latency),
            provider_request_id=raw.request_id if raw else None,
        )
        try:
            await self._ledger.record(call)
        except Exception:
            # Accounting must not turn a good answer into a failed run; the log keeps the gap.
            logger.exception("could not record an llm call", extra={"outcome": outcome})


def _parse(raw: RawReply, request: ModelRequest) -> tuple[BaseModel | None, str]:
    """The validated answer, or ``None`` and why: truncated, empty, not_json or schema."""
    if raw.truncated:
        return None, "truncated"
    if not raw.text.strip():
        return None, "empty"
    text = _FENCE.sub("", raw.text.strip())
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, "not_json"
    try:
        return request.response_model.model_validate(data), "ok"
    except ValidationError:
        return None, "schema"
