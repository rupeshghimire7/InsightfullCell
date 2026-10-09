"""Request and response models: the public API contract (exported to OpenAPI)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.domain.security import Role

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$", examples=["9f2c" + "0" * 60])]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- identity


class MeOut(BaseModel):
    user_id: UUID
    org_id: UUID
    role: Role
    org_name: str | None = None


# --------------------------------------------------------------------------- uploads


class CreateUploadRequest(_In):
    filename: str = Field(min_length=1, max_length=1024, examples=["home-sales-2026.csv"])
    size_bytes: int = Field(ge=1, description="Exact size of the file in bytes.")
    dataset_id: UUID | None = Field(
        default=None, description="Add the file as a new version of this dataset."
    )


class PartTargetOut(_Out):
    part_number: int = Field(ge=1)
    url: str = Field(description="Presigned PUT URL. Treat as a secret; it expires.")
    content_length: int = Field(description="Exact number of bytes the part must contain.")


class UploadOut(_Out):
    upload_id: UUID
    status: str
    filename: str
    format: str
    size_bytes: int
    part_size_bytes: int
    part_count: int
    expires_at: datetime
    parts: list[PartTargetOut] = Field(
        description="Present while the upload is pending; empty otherwise."
    )


class PresignPartsRequest(_In):
    part_numbers: list[int] = Field(min_length=1, max_length=100)


class CompletedPartIn(_In):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=256, description="ETag header from the part PUT.")


class CompleteUploadRequest(_In):
    sha256: Sha256Hex = Field(description="SHA-256 of the whole file, computed in the browser.")
    parts: list[CompletedPartIn] = Field(min_length=1, max_length=10_000)


# --------------------------------------------------------------------------- datasets


class VersionOut(_Out):
    id: UUID
    dataset_id: UUID
    sha256: str
    bytes: int
    format: str
    created_at: datetime


class DatasetOut(_Out):
    id: UUID
    name: str
    created_at: datetime
    version_count: int
    latest_version: VersionOut | None


class CompleteUploadResponse(_Out):
    dataset: DatasetOut
    version: VersionOut
    duplicate: bool = Field(
        description="True when identical bytes already existed: nothing new was stored."
    )


class DatasetPageOut(_Out):
    items: list[DatasetOut]
    next_cursor: str | None = None


class DatasetDetailOut(_Out):
    dataset: DatasetOut
    versions: list[VersionOut]
