"""Provider adapters and their default models.

Two tiers: ``strong`` (planning, narration, the question agent) and ``fast`` (routing, the
judge). Defaults can be overridden with ``LLM_MODEL_STRONG`` / ``LLM_MODEL_FAST``. Model ids
were checked against each provider's model list on 2026-10-10 (Gemini's again on 2026-10-17);
check them again before relying on them.

Gemini defaults to its Flash-Lite models: stable, on the free tier, and the cheapest that
take a JSON schema. Every answer is validated and every sentence verified, so a smaller model
costs fallbacks, never wrong numbers. ``gemini-3.8-flash`` is the step up.
"""

from __future__ import annotations

from typing import Final

from insightfulcell.config import Settings
from insightfulcell.llm.transport import Transport

DEFAULT_MODELS: Final[dict[str, dict[str, str]]] = {
    "gemini": {"strong": "gemini-3.5-flash-lite", "fast": "gemini-3.1-flash-lite"},
    "openai": {"strong": "gpt-6.1-sol", "fast": "gpt-6-luna"},
    # The system map names Sonnet for planning and narration and Haiku for routing and judging.
    "anthropic": {"strong": "claude-sonnet-5", "fast": "claude-haiku-5-5"},
    "deepseek": {"strong": "deepseek-v4-pro", "fast": "deepseek-v4-flash"},
}


def models_for(settings: Settings) -> dict[str, str]:
    defaults = DEFAULT_MODELS.get(settings.llm_provider, {"strong": "", "fast": ""})
    return {
        "strong": settings.llm_model_strong or defaults["strong"],
        "fast": settings.llm_model_fast or defaults["fast"],
    }


def build_transport(settings: Settings) -> Transport | None:
    """The configured provider's adapter, or ``None`` when the model is off (no provider or
    no API key): every run then plans with rules."""
    return transport_for(settings.llm_provider, settings)


def fallback_models(settings: Settings) -> dict[str, str]:
    """The backup model serves both tiers."""
    model = settings.llm_fallback_model or ""
    return {"strong": model, "fast": model}


def build_fallback_transport(settings: Settings) -> Transport | None:
    """The backup provider's adapter (``LLM_FALLBACK_PROVIDER``), or ``None`` when unset."""
    if not settings.llm_fallback_model:
        return None
    return transport_for(settings.llm_fallback_provider, settings)


def transport_for(provider: str, settings: Settings) -> Transport | None:
    key = settings.provider_key(provider)
    if provider == "none" or key is None or not key.get_secret_value():
        return None
    secret, timeout = key.get_secret_value(), settings.llm_timeout_seconds
    if provider == "gemini":
        from insightfulcell.llm.providers.gemini import GeminiTransport  # noqa: PLC0415

        return GeminiTransport(secret, timeout_seconds=timeout)
    if provider == "anthropic":
        from insightfulcell.llm.providers.anthropic import AnthropicTransport  # noqa: PLC0415

        return AnthropicTransport(secret, timeout_seconds=timeout)
    from insightfulcell.llm.providers.openai_compatible import (  # noqa: PLC0415
        OpenAICompatibleTransport,
    )

    if provider == "deepseek":
        return OpenAICompatibleTransport(
            provider="deepseek",
            api_key=secret,
            timeout_seconds=timeout,
            base_url=settings.deepseek_base_url,
            strict_schema=False,
        )
    return OpenAICompatibleTransport(provider="openai", api_key=secret, timeout_seconds=timeout)
