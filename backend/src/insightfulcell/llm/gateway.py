"""The LLM gateway (system map, AI harness): cost, privacy and reliability rules, written once.

Per call:

1. **Purpose -> model.** Planning, narration and the agent use the strong tier; routing and the
   judge use the fast tier.
2. **Breaker.** If the provider has been failing, fail fast.
3. **Payload check.** A value from a personal-data column, an email address, a US social
   security number or a payment card number anywhere in the prompt blocks the call and logs a
   security event (never the value).
4. **Budgets.** Per run (input tokens) and per organisation per month (all tokens). With a
   ``SpendGuard``, every attempt first reserves its worst-case cost (estimated input plus the
   full output limit, at the model's price) against the spend cap; a model without a price is
   never called. The reservation is settled with the real cost when the attempt ends.
5. **Call** with the provider's structured output, a timeout, and up to ``max_attempts``
   attempts with exponential backoff for transient errors. Calls are spaced to
   ``requests_per_minute`` when set (free tiers). A rate limit is waited out for as long as the
   provider asks (up to ``max_retry_wait_seconds``, ``max_rate_limit_waits`` times) and does not
   count as a failure; a quota used up for longer stops every call until it comes back.
6. **Validate** the answer with Pydantic on our side; an invalid answer is another attempt, and
   the log says which fields failed (never their content).
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
from decimal import ROUND_CEILING, Decimal
from typing import Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from insightfulcell.engine.ports import ModelReply, ModelRequest, ModelUnavailable, Purpose
from insightfulcell.llm.breaker import CircuitBreaker
from insightfulcell.llm.pricing import cost_usd, prices_for
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
#: The input estimate is rough (numbers and non-English text take more tokens), so the spend
#: reservation counts half as much again, plus a little for the provider's own framing.
RESERVE_INPUT_FACTOR: Final = Decimal("1.5")
RESERVE_INPUT_EXTRA_TOKENS: Final = 200
_MILLION: Final = Decimal(1_000_000)
_MICRO: Final = Decimal("0.000001")
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
    outcome: str  # ok, invalid, timeout, rate_limited, provider_error, refused, blocked, ...
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    provider_request_id: str | None = None
    fallback: bool = False  # made by the backup model after the main one failed


@dataclass(frozen=True, slots=True)
class SpendHold:
    """A reservation against the spend caps for one attempt. ``refused`` (``spend_cap``,
    ``spend_cap_provider`` or ``spend_cap_user``) when a cap has no room for it; then nothing
    is reserved."""

    id: UUID | None
    amount_usd: Decimal  # worst case: estimated input and the full output limit
    input_usd: Decimal  # the estimated input alone
    refused: str | None = None


class SpendGuard(Protocol):
    async def reserve(
        self,
        org_id: UUID,
        run_id: UUID | None,
        provider: str,
        amount_usd: Decimal,
        input_usd: Decimal,
    ) -> SpendHold: ...

    async def settle(
        self, hold: SpendHold, call: CallRecord, charged_usd: Decimal, *, estimated: bool
    ) -> None:
        """Release ``hold`` and record what the attempt cost (``estimated`` when the provider
        reported no usage, e.g. a timeout that may still have been billed)."""
        ...


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
    #: Spacing between calls (0: none). Free tiers allow a few requests a minute.
    requests_per_minute: int = 0
    #: Rate limits waited out per call, and the longest single wait worth taking.
    max_rate_limit_waits: int = 4
    max_retry_wait_seconds: float = 60.0
    #: How long calls stop after a quota is used up when the provider does not say.
    quota_pause_seconds: float = 3_600.0
    #: ``backup``: this gateway serves requests the main model could not (``FallbackModel``).
    role: str = "primary"


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
        spend: SpendGuard | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._ledger = ledger
        self._config = config
        self._breaker = breaker
        self._spend = spend
        self._sleep = sleep
        self._clock = clock
        self._pace_lock = asyncio.Lock()
        self._next_slot = 0.0
        self._quota_until: float | None = None

    @property
    def identity(self) -> tuple[str, str] | None:
        if self._transport is None:
            return None
        return self._transport.provider, self._config.models["strong"]

    @property
    def models(self) -> Mapping[str, str]:
        return self._config.models

    async def complete(self, request: ModelRequest) -> ModelReply:  # noqa: PLR0912, PLR0915
        # The whole call policy (budgets, retries, waits) reads best in one place.
        transport = self._transport
        if transport is None:
            raise ModelUnavailable("disabled", "No language model is configured.")
        model = self._config.models[TIER[request.purpose]]
        if self._quota_until is not None and self._clock() < self._quota_until:
            raise ModelUnavailable("quota_exhausted", "The model provider's quota is used up.")
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
            raise ModelUnavailable("budget_org", "The workspace's monthly model budget is spent.")

        reason = "provider_error"
        attempt = failures = waits = 0
        while failures < self._config.max_attempts:
            attempt += 1
            await self._pace()
            hold = await self._reserve(request, transport.provider, model, estimate)
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
                await self._record(
                    request, transport.provider, model, attempt, reason, None, started, hold
                )
                if reason == "rate_limited":
                    too_long = self._config.max_retry_wait_seconds
                    if exc.retry_after is not None and exc.retry_after > too_long:
                        # Asked to wait longer than a run should: a quota, in all but name.
                        self._pause_for_quota(exc.retry_after, transport.provider, model)
                        raise ModelUnavailable(
                            "quota_exhausted", "The model provider's quota is used up."
                        ) from exc
                    wait = self._rate_limit_wait(exc.retry_after, waits)
                    if wait is None:
                        raise ModelUnavailable(
                            "rate_limited", "The model provider kept refusing: too many requests."
                        ) from exc
                    waits += 1
                    await self._sleep(wait)
                    continue  # the provider's limit, not a fault: no failure counted
                self._breaker.failure()
                failures += 1
                if failures < self._config.max_attempts:
                    await self._sleep(self._backoff(failures))
                continue
            except PermanentProviderError as exc:
                await self._record(
                    request, transport.provider, model, attempt, exc.reason, None, started, hold
                )
                if exc.reason == "quota_exhausted":
                    self._pause_for_quota(exc.retry_after, transport.provider, model)
                    raise ModelUnavailable(
                        "quota_exhausted", "The model provider's quota is used up."
                    ) from exc
                self._breaker.failure()
                raise ModelUnavailable(
                    exc.reason, "The model provider refused the request."
                ) from exc

            parsed, cause, problems = _parse(raw, request)
            if parsed is None:
                reason = "invalid_output"
                failures += 1
                # Why, without any of the answer's content (it may echo the prompt).
                logger.warning(
                    "model answer unusable",
                    extra={
                        "purpose": request.purpose.value,
                        "cause": cause,
                        "attempt": attempt,
                        "output_tokens": raw.output_tokens,
                        "errors": problems,
                    },
                )
                await self._record(
                    request, transport.provider, model, attempt, "invalid", raw, started, hold
                )
                continue
            self._breaker.success()
            await self._record(
                request, transport.provider, model, attempt, "ok", raw, started, hold
            )
            return ModelReply(
                data=parsed,
                provider=transport.provider,
                model=model,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                attempts=attempt,
            )
        raise ModelUnavailable(reason, "No usable answer from the model.")

    async def check(self) -> None:
        """Prove the configured key and models work, without spending tokens. Raises the
        transport's ``PermanentProviderError`` (bad key, unknown model) or
        ``TransientProviderError`` (the provider could not be reached)."""
        if self._transport is None:
            return
        for model in sorted(set(self._config.models.values())):
            await self._transport.check(model)

    async def _reserve(
        self, request: ModelRequest, provider: str, model: str, estimate: int
    ) -> SpendHold | None:
        """Reserve this attempt's worst-case cost, or raise ``ModelUnavailable`` (fail closed:
        no price, no room under the cap, or the cap cannot be checked)."""
        if self._spend is None:
            return None
        prices = prices_for(model, self._config.prices)
        if prices is None:
            raise ModelUnavailable("price_unknown", "The model has no known price.")
        per_input, per_output = prices
        input_tokens = (
            Decimal(estimate) * RESERVE_INPUT_FACTOR + RESERVE_INPUT_EXTRA_TOKENS
        ).to_integral_value(rounding=ROUND_CEILING)
        input_usd = _usd(per_input * input_tokens)
        amount = _usd(per_input * input_tokens + per_output * self._config.max_output_tokens)
        try:
            hold = await self._spend.reserve(
                request.org_id, request.run_id, provider, amount, input_usd
            )
        except Exception as exc:
            logger.exception("could not check the model spend cap: not calling the model")
            raise ModelUnavailable(
                "spend_unavailable", "The spend cap could not be checked."
            ) from exc
        if hold.refused is not None:
            logger.warning(
                "model call skipped: the spend cap has no room for it",
                extra={
                    "reason": hold.refused,
                    "provider": provider,
                    "model": model,
                    "purpose": request.purpose.value,
                    "worst_case_usd": str(amount),
                },
            )
            raise ModelUnavailable(hold.refused, "The model spend cap is reached.")
        return hold

    @staticmethod
    def _charge(outcome: str, cost: Decimal | None, hold: SpendHold) -> tuple[Decimal, bool]:
        """What an attempt is counted as costing, and whether that is an estimate."""
        if cost is not None:
            return cost, False  # the provider reported its usage
        if outcome == "timeout":
            return hold.amount_usd, True  # it may have finished, and been billed, anyway
        if outcome in {"provider_error", "refused"}:
            return hold.input_usd, True  # the input may have been processed
        return Decimal(0), False  # refused before any work: rate limit, bad key, quota

    async def _pace(self) -> None:
        """Space calls ``60 / requests_per_minute`` seconds apart across every caller."""
        rpm = self._config.requests_per_minute
        if rpm <= 0:
            return
        async with self._pace_lock:
            now = self._clock()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + 60.0 / rpm
        if wait > 0:
            await self._sleep(wait)

    def _rate_limit_wait(self, retry_after: float | None, waits: int) -> float | None:
        """How long to wait before trying again, or ``None`` to give up."""
        if waits >= self._config.max_rate_limit_waits:
            return None
        wait = retry_after if retry_after is not None else self._backoff(waits + 1) * 4
        if wait > self._config.max_retry_wait_seconds:
            return None
        return wait + random.uniform(0, 0.5)  # noqa: S311  (jitter, not security)

    def _pause_for_quota(self, retry_after: float | None, provider: str, model: str) -> None:
        pause = retry_after if retry_after is not None else self._config.quota_pause_seconds
        pause = min(pause, self._config.quota_pause_seconds)
        self._quota_until = self._clock() + pause
        logger.error(
            "model quota used up: calls stop until it resets",
            extra={"provider": provider, "model": model, "pause_seconds": round(pause)},
        )

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
        hold: SpendHold | None = None,
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
            fallback=self._config.role == "backup",
        )
        try:
            await self._ledger.record(call)
        except Exception:
            # Accounting must not turn a good answer into a failed run; the log keeps the gap.
            logger.exception("could not record an llm call", extra={"outcome": outcome})
        if hold is None or self._spend is None:
            return
        charged, estimated = self._charge(outcome, call.cost_usd if raw else None, hold)
        try:
            await self._spend.settle(hold, call, charged, estimated=estimated)
        except Exception:
            # The reservation stays (and counts) until it expires, so the cap still holds.
            logger.exception("could not settle a model spend reservation")


def _usd(value: Decimal) -> Decimal:
    return (value / _MILLION).quantize(_MICRO, rounding=ROUND_CEILING)


def _parse(raw: RawReply, request: ModelRequest) -> tuple[BaseModel | None, str, list[str]]:
    """The validated answer, or ``None``, why (truncated, empty, not_json or schema) and, for a
    schema mismatch, where: field paths and error kinds only, never values."""
    if raw.truncated:
        return None, "truncated", []
    if not raw.text.strip():
        return None, "empty", []
    text = _FENCE.sub("", raw.text.strip())
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, "not_json", []
    try:
        return request.response_model.model_validate(data), "ok", []
    except ValidationError as exc:
        return None, "schema", schema_problems(exc)


def schema_problems(exc: ValidationError, limit: int = 12) -> list[str]:
    """``"dictionary.#.unit: missing"``-style lines, list positions folded into ``#``."""
    seen: dict[str, None] = {}
    for error in exc.errors(include_input=False, include_url=False):
        path = ".".join("#" if isinstance(part, int) else str(part) for part in error["loc"])
        seen.setdefault(f"{path or '(root)'}: {error['type']}", None)
    return list(seen)[:limit]
