"""Request and response models: the public API contract (exported to OpenAPI)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.domain.runs import EventType, Phase, Queue, RunStatus, StageStatus
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


class LatestRunOut(_Out):
    """The most recent analysis of a dataset, for lists."""

    id: UUID
    status: RunStatus
    stage: str | None
    created_at: datetime
    finished_at: datetime | None


class DatasetOut(_Out):
    id: UUID
    name: str
    created_at: datetime
    version_count: int
    latest_version: VersionOut | None
    latest_run: LatestRunOut | None = None


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


# --------------------------------------------------------------------------- runs


class CreateRunRequest(_In):
    dataset_id: UUID
    dataset_version_id: UUID | None = Field(
        default=None, description="The version to analyse. Defaults to the newest version."
    )


class RunFailureOut(_Out):
    """Why a run failed, in words safe to show (never cell values)."""

    code: str = Field(description="Stable machine-readable code.")
    detail: str | None = None
    hint: str | None = None


class StageOut(_Out):
    name: str
    phase: Phase
    position: int
    status: StageStatus
    reused: bool = Field(description="True when a checkpoint matched and the stage did not rerun.")
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    output: dict[str, Any] = Field(
        description="The stage's summary: counts, column names and types; never cell values."
    )


class RunSummaryOut(_Out):
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    dataset_name: str
    status: RunStatus
    phase: Phase
    stage: str | None = Field(description="What the run is doing now (or where it stopped).")
    queue: Queue
    attempts: int
    row_version: int = Field(description="Send as If-Match to change the run (ETag).")
    last_event_seq: int = Field(description="Sequence number of the newest event.")
    failure: RunFailureOut | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    review_requested_at: datetime | None


class RunOut(RunSummaryOut):
    stages: list[StageOut]
    events_url: str = Field(description="Server-Sent Events stream of this run's progress.")


class RunPageOut(_Out):
    items: list[RunSummaryOut]
    next_cursor: str | None = None


class RunEventOut(_Out):
    """One event as sent on the stream (``data:`` line, JSON)."""

    seq: int
    type: EventType
    stage: str | None
    data: dict[str, Any]
    at: datetime


# --------------------------------------------------------------------------- plans


class PreviewPairOut(_Out):
    before: str | None
    after: str | None
    rows: int


class CleaningOperationOut(_Out):
    id: str = Field(description="Stable id: the same change to the same column keeps it.")
    kind: str
    label: str = Field(description="What the change does, in plain words.")
    column: str | None = Field(description="Null for a change to whole rows (duplicates).")
    params: dict[str, Any]
    lossy: bool = Field(description="True when the change can remove or replace data.")
    default: Literal["auto", "review", "opt_in"]
    decision: Literal["accepted", "rejected", "pending"]
    reason: str
    source: Literal["rules", "model", "person"]
    rows_affected: int
    values_lost: int = Field(description="Non-empty values the change would empty.")
    preview: list[PreviewPairOut] = Field(
        description="Up to five before/after pairs. Personal data is masked."
    )
    notice: str | None


class CleaningPlanOut(_Out):
    revision: int = Field(description="Send back with a decision; 412 if it changed.")
    policy: Literal["always_review", "review_lossy", "auto_safe"]
    status: Literal["proposed", "approved"]
    decided_by_you: bool
    decided_at: datetime | None
    operations: list[CleaningOperationOut]


class PlanColumnOut(_Out):
    position: int
    name: str
    physical_type: str
    meaning: str
    confidence: Literal["high", "medium", "low"]
    personal_data: str | None = Field(description="Set when the column is masked.")
    unit: str | None
    display_name: str | None
    role: str | None
    flags: list[str] = Field(description="Quality flags, e.g. case_variants, sentinel_values.")
    profile: dict[str, Any] = Field(
        description="Statistics for the column. Counts only for personal-data columns."
    )


class DictionaryEntryOut(_Out):
    column: str
    display_name: str
    description: str
    unit: str | None
    meaning: str
    role: str
    personal_data: str | None
    source: Literal["rules", "model", "person"]


class TargetOut(_Out):
    column: str
    kind: Literal["numeric", "binary", "categorical"]
    rationale: str
    source: Literal["rules", "model", "person"]


class FeatureOut(_Out):
    id: str
    kind: str
    columns: list[str]
    part: str | None
    bins: int | None
    name: str
    reason: str
    source: Literal["rules", "model", "person"]
    enabled: bool


class AnalysisItemOut(_Out):
    id: str
    kind: str
    columns: list[str]
    question: str
    priority: int
    source: Literal["rules", "model", "person"]


class ChartItemOut(_Out):
    id: str
    kind: str
    x: str
    y: str | None
    group: str | None
    aggregate: str | None
    title: str
    source: Literal["rules", "model", "person"]


class RejectionOut(_Out):
    section: str
    item: str
    reason: str


class PlanOut(_Out):
    """The run's resolved analysis plan, its columns, its cleaning plan, and exactly what the
    planning model was shown."""

    run: RunOut
    revision: int
    source: Literal["model", "cache", "rules", "person"] = Field(
        description="Where the plan came from: the model, the plan cache, the rules, or a "
        "person's revision."
    )
    provider: str | None
    model: str | None
    prompt_version: str | None
    fallback_reason: str | None = Field(
        description="Why the rule-based planner was used instead of the model, if it was."
    )
    dataset_summary: str
    domain: str
    grain: str
    dictionary: list[DictionaryEntryOut]
    target: TargetOut | None
    features: list[FeatureOut]
    analyses: list[AnalysisItemOut]
    charts: list[ChartItemOut]
    relevance: dict[str, float]
    rejected: list[RejectionOut] = Field(description="Model proposals that failed validation.")
    model_input: dict[str, Any] | None = Field(
        description="The masked schema card sent to the planning model (no rows, no personal "
        "values)."
    )
    columns: list[PlanColumnOut]
    cleaning: CleaningPlanOut | None


class CleaningDecisionIn(_In):
    operation_id: str = Field(min_length=1, max_length=1100)
    accept: bool


class ColumnEditIn(_In):
    name: str = Field(min_length=1, max_length=1000)
    display_name: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=16, description="Empty string clears it.")
    excluded: bool | None = Field(
        default=None, description="Leave out of (or bring back into) the analysis."
    )


class DecideCleaningRequest(_In):
    cleaning_revision: int = Field(ge=1, description="The cleaning plan revision you reviewed.")
    decisions: list[CleaningDecisionIn] = Field(max_length=5_000)
    columns: list[ColumnEditIn] = Field(default_factory=list, max_length=500)
    target: str | None = Field(default=None, max_length=1000)
    no_target: bool = Field(default=False, description="This dataset has no outcome to explain.")
    disabled_features: list[str] | None = Field(default=None, max_length=200)
