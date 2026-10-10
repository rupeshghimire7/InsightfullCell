"""The LLM gateway: the only code path allowed to call a language model (ADR-031).

``gateway.LLMGateway`` implements the engine's ``LanguageModel`` port: purpose -> model, run
and monthly budgets, the personal-data payload check, retries with backoff, a circuit breaker,
validation of every answer and an ``llm_calls`` row per attempt. ``providers`` hold one
adapter per provider (Gemini, OpenAI, Anthropic, DeepSeek), each using its official SDK.
"""
