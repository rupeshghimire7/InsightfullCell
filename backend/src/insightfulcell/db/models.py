"""ORM models for the tables the API writes. The migrations hold the authoritative DDL
(constraints, RLS, grants); these mappings only describe columns for queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Integer, LargeBinary, Text, text
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
