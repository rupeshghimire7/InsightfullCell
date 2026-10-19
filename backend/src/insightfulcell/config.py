"""Typed, validated configuration. Every setting comes from the environment (12-factor).

Security-critical settings (database URL, token issuer and JWKS URL) have no defaults, so a
misconfigured deployment fails at start-up instead of running open.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    # --- database (application role: NOT the table owner, NOBYPASSRLS)
    database_url: str
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_statement_timeout_ms: int = Field(default=5_000, ge=100)

    # --- token verification (tokens are issued by the web app's Better Auth)
    auth_jwks_url: str
    auth_issuer: str
    auth_audience: str = "insightfulcell-api"
    # Run-scoped event-stream tickets (5 minutes, one run) carry their own audience, so a ticket
    # can never be used as an API token and an API token can never open an event stream.
    auth_events_audience: str = "insightfulcell-events"
    auth_clock_skew_seconds: int = Field(default=30, ge=0, le=300)

    # --- live progress (IC-008). Browsers open event streams on the API directly, so the web
    # app's origin must be allowed here (comma-separated). Applies to the event stream only.
    events_allowed_origins: str = "http://localhost:3000"
    events_keepalive_seconds: float = Field(default=15.0, gt=0, le=120)
    events_listen: bool = True  # one LISTEN connection per API instance; off in some tests

    # --- object storage (RustFS locally, S3 in the cloud: configuration only, ADR-009, ADR-027)
    s3_bucket: str = "insightfulcell-dev"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    # Hostname the *browser* must use in presigned URLs when it differs from the one the API
    # uses (e.g. http://localhost:9000 vs http://s3:9000 inside Docker). Defaults to the above.
    s3_public_endpoint_url: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_force_path_style: bool = False
    # Files of a deleted run or dataset stay in storage this many days (then the worker removes
    # them); the database rows go at once (ADR-037). 0 removes them at the next hourly sweep.
    storage_purge_after_days: int = Field(default=7, ge=0, le=3_650)

    # --- uploads (FR-INGEST-01)
    max_upload_bytes: int = Field(default=250 * MIB, ge=1)
    upload_part_bytes: int = Field(default=8 * MIB, ge=5 * MIB)  # S3 minimum part size is 5 MiB
    upload_url_ttl_seconds: int = Field(default=3_600, ge=60, le=43_200)
    upload_expiry_hours: int = Field(default=24, ge=1)

    # --- runs and the worker (IC-007)
    run_max_attempts: int = Field(default=3, ge=1, le=10)
    run_crashes_before_large_queue: int = Field(default=2, ge=1, le=10)
    run_reconcile_after_seconds: int = Field(default=60, ge=10)
    run_stalled_after_seconds: int = Field(default=60, ge=15)
    run_retry_delay_seconds: int = Field(default=15, ge=0, le=3_600)
    run_capacity_wait_seconds: int = Field(default=10, ge=1, le=600)
    run_review_reminder_hours: int = Field(default=24, ge=1)
    run_review_expiry_days: int = Field(default=7, ge=1)
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_scratch_dir: Path | None = None  # default: the system temp directory
    idempotency_key_ttl_hours: int = Field(default=24, ge=1)

    # --- LLM gateway (ADR-031, ADR-036). One provider at a time. Choosing a provider in the
    # environment (LLM_PROVIDER) requires its API key: start-up fails without it, and the
    # worker checks the key and models before taking work. Left at the default without a key,
    # the model is off and every run uses the rule-based planner (a complete, correct fallback).
    llm_provider: Literal["gemini", "openai", "anthropic", "deepseek", "none"] = "gemini"
    # Override the provider's default model for each tier ("strong" plans and narrates, "fast"
    # routes questions and judges). Defaults live in ``llm/providers/__init__.py``.
    llm_model_strong: str | None = None
    llm_model_fast: str | None = None
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    llm_max_attempts: int = Field(default=3, ge=1, le=5)
    llm_max_output_tokens: int = Field(default=16_000, ge=1_000, le=64_000)
    llm_run_input_token_budget: int = Field(default=120_000, ge=1_000)
    llm_breaker_failures: int = Field(default=5, ge=1, le=100)
    llm_breaker_open_seconds: float = Field(default=60.0, gt=0, le=3_600)
    # Rate limits: calls are spaced to this many a minute (0: no spacing; set it to a free
    # tier's limit), and a refused call waits as long as the provider asks, up to the longest
    # wait below, this many times. A longer wait means a used-up quota: calls stop until then.
    llm_requests_per_minute: int = Field(default=0, ge=0, le=10_000)
    llm_rate_limit_waits: int = Field(default=4, ge=0, le=20)
    llm_max_retry_wait_seconds: float = Field(default=60.0, gt=0, le=600)
    # Narration (Phase 5): report sections narrated at the same time, and the judge (a second
    # model that can only reject a verified sentence). The judge stays off until its spike
    # shows it catches what the verifier misses without rejecting good sentences.
    llm_narration_concurrency: int = Field(default=4, ge=1, le=16)
    llm_judge: bool = False
    # Prices (USD per million input / output tokens) for cost accounting, as JSON:
    # {"model-id": [input, output]}. Merged over the built-in table; unknown models cost null.
    llm_prices_json: str = "{}"
    # Size of the masked schema card sent to the planner (estimated tokens). Wide tables are
    # summarised in tiers to fit, never truncated silently.
    schema_card_token_budget: int = Field(default=9_000, ge=1_000, le=60_000)

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+psycopg://' scheme")
        return value

    @field_validator("llm_prices_json")
    @classmethod
    def _prices(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM_PRICES_JSON must be JSON") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(v, list)
            and len(v) == 2
            and all(isinstance(p, int | float) and p >= 0 for p in v)
            for v in parsed.values()
        ):
            raise ValueError('LLM_PRICES_JSON must look like {"model": [input, output]}')
        return value

    @property
    def llm_prices(self) -> dict[str, tuple[float, float]]:
        return {k: (float(v[0]), float(v[1])) for k, v in json.loads(self.llm_prices_json).items()}

    @property
    def events_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.events_allowed_origins.split(",") if o.strip()]

    @property
    def llm_api_key(self) -> SecretStr | None:
        """The key of the configured provider, or ``None`` (the model is then off)."""
        return {
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "deepseek": self.deepseek_api_key,
            "none": None,
        }[self.llm_provider]

    @property
    def psycopg_conninfo(self) -> str:
        """``DATABASE_URL`` for direct psycopg connections (queue, LISTEN)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @model_validator(mode="after")
    def _chosen_provider_has_a_key(self) -> Settings:
        if "llm_provider" not in self.model_fields_set or self.llm_provider == "none":
            return self
        key = self.llm_api_key
        if key is None or not key.get_secret_value().strip():
            name = f"{self.llm_provider.upper()}_API_KEY"
            raise ValueError(
                f"LLM_PROVIDER is {self.llm_provider!r} but {name} is not set: add the key, "
                "or set LLM_PROVIDER=none to plan and write with rules only"
            )
        return self

    @model_validator(mode="after")
    def _distinct_audiences(self) -> Settings:
        if self.auth_events_audience == self.auth_audience:
            raise ValueError("AUTH_EVENTS_AUDIENCE must differ from AUTH_AUDIENCE")
        return self

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if not self.auth_jwks_url.startswith("https://"):
                raise ValueError("AUTH_JWKS_URL must be https in production")
            if not self.auth_issuer.startswith("https://"):
                raise ValueError("AUTH_ISSUER must be https in production (the web app's URL)")
            if self.s3_endpoint_url is not None:
                raise ValueError("S3_ENDPOINT_URL must be unset in production (use AWS S3)")
            if any(not o.startswith("https://") for o in self.events_origins):
                raise ValueError("EVENTS_ALLOWED_ORIGINS must list https origins in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
