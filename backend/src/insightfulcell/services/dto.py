"""Plain result types returned by services (kept free of API/pydantic concerns)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
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
class LatestRun:
    id: UUID
    status: str
    stage: str | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    id: UUID
    name: str
    created_at: datetime
    version_count: int
    latest_version: VersionInfo | None
    latest_run: LatestRun | None = None


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


@dataclass(frozen=True, slots=True)
class RunFailure:
    code: str
    detail: str | None
    hint: str | None


@dataclass(frozen=True, slots=True)
class StageInfo:
    name: str
    phase: str
    position: int
    status: str
    reused: bool
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    output: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Drilldown:
    """The subset a drill-down analyses: one column and the groups kept."""

    parent_run_id: UUID
    column: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    dataset_name: str
    status: str
    phase: str
    stage: str | None
    queue: str
    attempts: int
    row_version: int
    last_event_seq: int
    failure: RunFailure | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    review_requested_at: datetime | None
    drilldown: Drilldown | None = None
    review_mode: str = "manual"


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: RunSummary
    stages: tuple[StageInfo, ...]
    #: Queued while no worker is alive: nothing will happen until one starts.
    waiting_for_worker: bool = False


@dataclass(frozen=True, slots=True)
class RunPage:
    items: tuple[RunSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RunAccepted:
    detail: RunDetail
    created: bool  # False when an Idempotency-Key replay returned the original run


@dataclass(frozen=True, slots=True)
class RunEventInfo:
    seq: int
    type: str
    stage: str | None
    data: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunEventBatch:
    events: tuple[RunEventInfo, ...]
    status: str  # the run's status when the batch was read
