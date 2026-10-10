"""ORM models for the tables the API writes. The migrations hold the authoritative DDL
(constraints, RLS, grants); these mappings only describe columns for queries."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_NOW = text("now()")


class Base(DeclarativeBase):
    __table_args__ = {"schema": "app"}  # noqa: RUF012  (SQLAlchemy convention)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, server_default="3")
    cleaning_policy: Mapped[str] = mapped_column(Text, server_default="review_lossy")
    monthly_token_budget: Mapped[int] = mapped_column(BigInteger, server_default="20000000")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    filename: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    declared_bytes: Mapped[int] = mapped_column(BigInteger)
    part_size_bytes: Mapped[int] = mapped_column(Integer)
    part_count: Mapped[int] = mapped_column(Integer)
    staging_key: Mapped[str] = mapped_column(Text)
    s3_upload_id: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    upload_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    bytes: Mapped[int] = mapped_column(BigInteger)
    format: Mapped[str] = mapped_column(Text)
    bronze_key: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)
    purged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    method: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text)
    response_code: Mapped[int | None] = mapped_column(Integer, default=None)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    phase: Mapped[str] = mapped_column(Text, server_default="A")
    stage: Mapped[str | None] = mapped_column(Text, default=None)
    queue: Mapped[str] = mapped_column(Text, server_default="standard")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    crash_count: Mapped[int] = mapped_column(Integer, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(Text, default=None)
    failure_detail: Mapped[str | None] = mapped_column(Text, default=None)
    failure_hint: Mapped[str | None] = mapped_column(Text, default=None)
    event_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")
    code_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    review_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    review_reminded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class RunStage(Base):
    __tablename__ = "run_stages"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    phase: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    checkpoint_key: Mapped[str | None] = mapped_column(Text, default=None)
    code_version: Mapped[str | None] = mapped_column(Text, default=None)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    reused: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class RunEvent(Base):
    __tablename__ = "run_events"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text, default=None)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=_NOW, primary_key=True
    )


class RunColumn(Base):
    __tablename__ = "run_columns"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    physical_type: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(Text)
    personal_data: Mapped[str | None] = mapped_column(Text, default=None)
    unit: Mapped[str | None] = mapped_column(Text, default=None)
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    role: Mapped[str | None] = mapped_column(Text, default=None)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class AnalysisPlan(Base):
    __tablename__ = "analysis_plans"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    schema_fingerprint: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text, default=None)
    provider: Mapped[str | None] = mapped_column(Text, default=None)
    model: Mapped[str | None] = mapped_column(Text, default=None)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB)
    plan_hash: Mapped[str] = mapped_column(Text)
    rejected: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    input_card: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    fallback_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class CleaningPlan(Base):
    __tablename__ = "cleaning_plans"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    plan_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class CleaningOp(Base):
    __tablename__ = "cleaning_ops"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    op_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    column_name: Mapped[str | None] = mapped_column(Text, default=None)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    lossy: Mapped[bool] = mapped_column(Boolean)
    default_mode: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    rows_affected: Mapped[int] = mapped_column(BigInteger, server_default="0")
    values_lost: Mapped[int] = mapped_column(BigInteger, server_default="0")
    preview: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    notice: Mapped[str | None] = mapped_column(Text, default=None)


class DatasetPreferences(Base):
    __tablename__ = "dataset_preferences"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_NOW)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_id: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(SmallInteger)
    outcome: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    provider_request_id: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=_NOW, primary_key=True
    )


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), server_default="0")
    calls: Mapped[int] = mapped_column(Integer, server_default="0")
