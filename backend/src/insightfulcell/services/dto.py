"""Plain result types returned by services (kept free of API/pydantic concerns)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PartTarget:
    part_number: int
    url: str
    content_length: int


@dataclass(frozen=True, slots=True)
class UploadTicket:
    upload_id: UUID
    status: str
    filename: str
    format: str
    size_bytes: int
    part_size_bytes: int
    part_count: int
    expires_at: datetime
    parts: tuple[PartTarget, ...]  # empty unless the upload is still pending


@dataclass(frozen=True, slots=True)
class VersionInfo:
    id: UUID
    dataset_id: UUID
    sha256: str
    bytes: int
    format: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    id: UUID
    name: str
    created_at: datetime
    version_count: int
    latest_version: VersionInfo | None


@dataclass(frozen=True, slots=True)
class CompletedUpload:
    dataset: DatasetInfo
    version: VersionInfo
    duplicate: bool  # True when identical bytes already existed and nothing new was stored


@dataclass(frozen=True, slots=True)
class DatasetPage:
    items: tuple[DatasetInfo, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DatasetDetail:
    dataset: DatasetInfo
    versions: tuple[VersionInfo, ...]
