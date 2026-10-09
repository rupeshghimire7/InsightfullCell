"""ORM models for the tables the API writes. The migrations hold the authoritative DDL
(constraints, RLS, grants); these mappings only describe columns for queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Integer, LargeBinary, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
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
