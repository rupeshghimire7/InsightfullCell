"""Request and response models: the public API contract (exported to OpenAPI)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class DeletedOut(_Out):
    """What a delete removed. The rows are gone already; the files follow later."""

    run_ids: list[UUID] = Field(description="Every run that was deleted with it.")
    files_removed_after: datetime = Field(
        description="When the worker removes the files from storage (STORAGE_PURGE_AFTER_DAYS)."
    )


# --------------------------------------------------------------------------- runs


class CreateRunRequest(_In):
    dataset_id: UUID
    dataset_version_id: UUID | None = Field(
        default=None, description="The version to analyse. Defaults to the newest version."
    )
    review_mode: Literal["auto", "manual"] = Field(
        default="auto",
        description="auto: every change that would wait for a review is applied and the run "
        "goes on to the report without stopping. manual: the run stops for your review.",
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


class DrilldownOut(_Out):
    """The subset a drill-down analyses, chosen from its parent's report."""

    parent_run_id: UUID
    column: str
    values: list[str]


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
    drilldown: DrilldownOut | None = Field(
        default=None, description="Set for an exploratory run of a subset of another run."
    )
    review_mode: Literal["auto", "manual"] = Field(
        default="manual", description="Whether the run stops for a person's review."
    )


class RunOut(RunSummaryOut):
    stages: list[StageOut]
    events_url: str = Field(description="Server-Sent Events stream of this run's progress.")
    waiting_for_worker: bool = Field(
        default=False,
        description="The run is queued and no worker is running, so it cannot start until "
        "one does.",
    )


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


class RequestOutcomeOut(_Out):
    request: str = Field(description="The request in the person's own words.")
    status: Literal["added", "removed", "already_planned", "not_understood"]
    note: str = Field(description="What was done with it, in plain words.")
    source: Literal["model", "rules"] = Field(
        description="Who read the request: the planning model or the engine's own reading."
    )


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
    requests: list[RequestOutcomeOut] = Field(
        description="The person's requests and what the planner did with each."
    )
    removed_analyses: list[AnalysisItemOut] = Field(
        description="Planned questions the person removed (they can be brought back)."
    )


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


class RevisePlanRequest(_In):
    """Ask for the plan to be made again with your changes; the run comes back for review."""

    cleaning_revision: int = Field(ge=1, description="The cleaning plan revision you reviewed.")
    decisions: list[CleaningDecisionIn] = Field(
        default_factory=list,
        max_length=5_000,
        description="Cleaning decisions made so far (any subset); they are kept.",
    )
    columns: list[ColumnEditIn] = Field(default_factory=list, max_length=500)
    target: str | None = Field(default=None, max_length=1000)
    no_target: bool = False
    disabled_features: list[str] | None = Field(default=None, max_length=200)
    removed_analyses: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Ids of planned questions you do not want. Leave one out to bring it back.",
    )
    requests: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list,
        max_length=5,
        description="What else to analyse or drop, in your own words (up to five).",
    )


# --------------------------------------------------------------------------- report


class SentenceOut(_Out):
    text: str
    citations: list[str] = Field(description="Ids of the facts the sentence rests on.")
    written_by: Literal["model", "engine"] | None = Field(
        default=None,
        description="model: written by the language model and verified; engine: the fact's own "
        "statement (a template).",
    )


ValueFormat = Literal["number", "percent", "count", "strength", "signed_percent"]


class ChartPointOut(_Out):
    label: str
    value: float
    x: float | None
    x2: float | None
    n: int | None
    key: str | None = Field(
        default=None,
        description="The group this point is, as the data writes it, when it can be explored "
        "on its own (POST /v1/runs/{id}/drilldowns with the chart's drill_column).",
    )


class ChartCheckOut(_Out):
    claims: int = Field(description="Values recomputed from the data, one query each.")
    verified: bool


class ChartOut(_Out):
    kind: Literal["bar", "line", "scatter", "histogram"]
    title: str
    x_label: str
    y_label: str
    value_format: ValueFormat
    unit: str | None
    points: list[ChartPointOut]
    note: str | None
    drill_column: str | None = Field(
        default=None, description="The column a point's key belongs to, if explorable."
    )
    check: ChartCheckOut | None = Field(
        default=None, description="The visualize stage's check (absent on older reports)."
    )
    vega_lite: dict[str, Any] | None = Field(
        default=None, description="The chart as a Vega-Lite spec, values inline."
    )


class MapRegionOut(_Out):
    code: str = Field(description="ISO 3166 code.")
    name: str
    path: str = Field(description="SVG path data in the map's view box (fill-rule evenodd).")
    status: Literal["value", "few_rows", "no_rows"] = Field(
        description="few_rows: fewer than ten rows, drawn hatched and without a number."
    )
    value: float | None
    n: int | None
    key: str | None


class MapOut(_Out):
    title: str
    value_label: str
    value_format: ValueFormat
    unit: str | None
    place_label: str = Field(default="", description="The place column's display name.")
    level: Literal["country", "state"]
    country: str | None
    width: float
    height: float
    regions: list[MapRegionOut]
    note: str | None
    drill_column: str | None
    reference_set: str
    check: ChartCheckOut | None


class ColumnRefOut(_Out):
    name: str
    display_name: str


class ReportSectionOut(_Out):
    analysis_id: str
    question: str
    kind: str
    kind_label: str
    requested_by_person: bool
    columns: list[ColumnRefOut]
    answer: list[SentenceOut]
    written_by: Literal["model", "engine"] | None
    chart: ChartOut | None
    facts: list[str]
    skipped: str | None = Field(description="Why the question could not be answered, if not.")
    maps: list[MapOut] = Field(default_factory=list)
    chart_withheld: bool = Field(
        default=False, description="A chart was drawn but one of its values did not match."
    )


class UnderstandingOut(_Out):
    summary: str
    domain: str
    grain: str
    target: str | None
    rows: int
    columns: int
    masked_columns: int
    planned_by: Literal["model", "cache", "rules"]


class CleaningStepOut(_Out):
    label: str
    column: str | None
    rows: int


class PreparationOut(_Out):
    rows_before: int
    rows_after: int
    applied: list[CleaningStepOut]
    not_applied: int
    reviewed_by_person: bool


class NarrationOut(_Out):
    source: Literal["model", "engine", "mixed"]
    provider: str | None
    model: str | None
    fallback_reason: str | None
    sentences_kept: int
    sentences_dropped: int
    sentences_repaired: int = 0
    simplified: bool = Field(
        default=False,
        description="The model was unavailable for some part, so it was written from the "
        "facts' own statements.",
    )
    retryable: bool = Field(
        default=False, description="Narrating again (report:renarrate) may succeed."
    )
    flagged: list[str] = Field(
        default_factory=list,
        description="Parts written from templates because too many sentences failed the checks.",
    )
    judge: Literal["off", "on"] = "off"
    rejected: dict[str, int] = Field(
        default_factory=dict,
        description="Rejected sentences per check: form, citation, stray_number, direction, "
        "strength, causal, condition, judge.",
    )
    prompt_version: str | None = None


class FactTestOut(_Out):
    """The hypothesis test a fact rests on, after the run's false-discovery control."""

    method: str
    effect_name: str = Field(description="spearman, eta, cramers_v, kendall_tau, ...")
    effect: float
    ci_low: float | None = Field(description="Lower end of the 95% interval.")
    ci_high: float | None
    p_value: float
    q_value: float = Field(description="After Benjamini-Hochberg across the run's tests.")


class FactCheckOut(_Out):
    """The sensitivity check: the fact re-tested on minimally cleaned data."""

    outcome: Literal["holds", "weakens", "flips", "not_retested", "same_data"]
    note: str
    effect: float | None = Field(description="The effect on minimally cleaned data.")
    p_value: float | None


class FactOut(_Out):
    id: str
    analysis_id: str
    kind: str
    columns: list[ColumnRefOut]
    rows: int = Field(description="Rows the numbers rest on.")
    method: str
    statement: str
    values: dict[str, Any]
    tier: Literal["descriptive", "strong", "supported", "inconclusive"] = Field(
        description="strong: q at most 0.01; supported: q at most 0.05; inconclusive: did not "
        "pass; descriptive: no test (a count, share or summary)."
    )
    test: FactTestOut | None
    check: FactCheckOut | None
    content_hash: str = Field(description="SHA-256 of the fact's content, fixed when stored.")
    exploratory: bool = Field(
        default=False, description="Computed in a drill-down: a lead to check, not a conclusion."
    )


class EvidenceOut(_Out):
    tests: int = Field(description="Hypothesis tests in the run's false-discovery control.")
    reliable: int = Field(description="Tests that passed at the run's false-discovery rate.")
    alpha: float
    rechecked: int = Field(description="Top findings re-tested on minimally cleaned data.")


class FeatureNoteOut(_Out):
    label: str
    outcome: Literal["kept", "no_signal", "redundant", "not_computed", "no_target"]
    redundant_with: str | None = None


class ScopeOut(_Out):
    """The subset a drill-down analysed."""

    parent_run_id: str
    column: str
    display_name: str
    values: list[str]
    rows: int


class VisualsOut(_Out):
    charts: int
    withheld: int
    maps: int
    claims: int = Field(description="Chart and map values recomputed from the data.")


class DataColumnOut(_Out):
    name: str
    type: str = Field(description="The column's type as read (DuckDB type name).")
    personal_data: str | None = Field(
        description="The kind of personal data when the column is hidden (its cells are null)."
    )


class DataPageOut(_Out):
    columns: list[DataColumnOut]
    rows: list[list[str | int | float | bool | None]] = Field(
        description="One list per row, in column order, in the file's order. Long text is cut; "
        "integers beyond 2^53, decimals, dates and times are text."
    )
    total_rows: int
    offset: int


class ReportOut(_Out):
    """A finished run's report: every sentence cites the facts it rests on."""

    run: RunOut
    revision: int
    created_at: datetime
    title: str
    dataset_name: str
    understanding: UnderstandingOut
    preparation: PreparationOut
    requests: list[RequestOutcomeOut]
    removed_questions: int
    summary: list[SentenceOut]
    sections: list[ReportSectionOut]
    narration: NarrationOut
    method_notes: list[str]
    highlights: list[str] = Field(
        default_factory=list, description="Fact ids of the best findings, best first."
    )
    evidence: EvidenceOut | None = None
    features: list[FeatureNoteOut] = Field(default_factory=list)
    scope: ScopeOut | None = Field(
        default=None, description="Set for a drill-down: the subset, findings exploratory."
    )
    visuals: VisualsOut | None = None
    has_dashboard: bool = Field(default=False, description="GET /dashboard is available.")
    facts: list[FactOut]


class TileOut(_Out):
    fact_id: str
    analysis_id: str
    label: str
    value: str = Field(description="One of the fact's own numbers, as the fact writes it.")
    question: str
    statement: str
    tier: Literal["descriptive", "strong", "supported", "inconclusive"]


class InsightOut(_Out):
    analysis_id: str
    question: str
    kind_label: str
    chart: ChartOut | None = None
    map: MapOut | None = None
    facts: list[str]


class SuggestedQuestionOut(_Out):
    analysis_id: str
    question: str
    answer: list[SentenceOut] = Field(description="The report's verified sentences.")
    written_by: Literal["model", "engine"] | None = None


class GlanceOut(_Out):
    rows: int
    columns: int
    tests: int
    reliable: int
    charts_verified: int
    charts_withheld: int


class DashboardOut(_Out):
    """A finished run's dashboard: headline tiles, top charts, maps, a gallery and suggested
    questions answered with the report's verified sentences. Every number cites a fact."""

    run: RunOut
    revision: int
    created_at: datetime
    title: str
    dataset_name: str
    glance: GlanceOut
    tiles: list[TileOut]
    insights: list[InsightOut]
    maps: list[InsightOut]
    gallery: list[InsightOut]
    questions: list[SuggestedQuestionOut]
    exploratory: bool
    scope: ScopeOut | None = None
    narration: NarrationOut
    facts: list[FactOut] = Field(description="The facts the dashboard cites.")


class CreateDrilldownRequest(_In):
    column: str = Field(min_length=1, max_length=300, description="The chart's drill_column.")
    values: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=20, description="Point keys (or map region keys) to keep."
    )
