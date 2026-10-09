"""Typed, validated configuration. Every setting comes from the environment (12-factor).

Security-critical settings (database URL, token issuer and JWKS URL) have no defaults, so a
misconfigured deployment fails at start-up instead of running open.
"""

from __future__ import annotations

from functools import lru_cache
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
    auth_clock_skew_seconds: int = Field(default=30, ge=0, le=300)

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

    # --- uploads (FR-INGEST-01)
    max_upload_bytes: int = Field(default=250 * MIB, ge=1)
    upload_part_bytes: int = Field(default=8 * MIB, ge=5 * MIB)  # S3 minimum part size is 5 MiB
    upload_url_ttl_seconds: int = Field(default=3_600, ge=60, le=43_200)
    upload_expiry_hours: int = Field(default=24, ge=1)

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+psycopg://' scheme")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if not self.auth_jwks_url.startswith("https://"):
                raise ValueError("AUTH_JWKS_URL must be https in production")
            if self.s3_endpoint_url is not None:
                raise ValueError("S3_ENDPOINT_URL must be unset in production (use AWS S3)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
