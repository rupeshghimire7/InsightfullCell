"""Model prices for cost accounting (USD per million input / output tokens).

Only prices we could check are listed; an unknown model is recorded with tokens and a null
cost rather than a guessed one. ``LLM_PRICES_JSON`` adds or overrides entries. The spend cap
never calls a model without a price (see ``LLMGateway``).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal
from typing import Final

BUILT_IN: Final[dict[str, tuple[float, float]]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
    "deepseek-v4-flash": (0.14, 0.28),
    # Gemini paid-tier list prices, checked 2026-10-17 (free-tier calls are not billed; the
    # 3.8 and 3.6 Flash prices double on 2027-01-01).
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.8-flash": (0.75, 3.75),
    # OpenAI list prices, checked 2026-10-21; gpt-4o-mini shuts down on 2027-01-14.
    "gpt-4o-mini": (0.15, 0.60),
}

_MILLION: Final = Decimal(1_000_000)
_MICRO: Final = Decimal("0.000001")


def prices_for(
    model: str, overrides: Mapping[str, tuple[float, float]] | None = None
) -> tuple[Decimal, Decimal] | None:
    """(input, output) USD per million tokens, or ``None`` for a model without a price."""
    prices = {**BUILT_IN, **(overrides or {})}.get(model)
    if prices is None:
        return None
    per_input, per_output = prices
    return Decimal(str(per_input)), Decimal(str(per_output))


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: Mapping[str, tuple[float, float]] | None = None,
) -> Decimal | None:
    prices = prices_for(model, overrides)
    if prices is None:
        return None
    per_input, per_output = prices
    total = (per_input * input_tokens + per_output * output_tokens) / _MILLION
    # Rounded up, never down: a cost is never under-counted.
    return total.quantize(_MICRO, rounding=ROUND_CEILING)
