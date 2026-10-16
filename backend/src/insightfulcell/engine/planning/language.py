"""Plan language v1.

Two shapes:

- ``ProposedPlan``: what a planner returns. The model's reply is parsed into it leniently, so
  one bad item never throws away a whole plan: no length limits, a value left out where null
  would do counts as null (some providers omit null properties), and an item that still does
  not fit is dropped and counted (``dropped``). The validator then accepts or rejects each
  remaining item with a reason.
- ``ResolvedPlan``: the merged, validated plan a run is frozen with. Every item says where it
  came from (rules, model, person) and has a stable id, so a person's choices (a feature
  switched off) carry over to the next run of the same dataset.

Every option is a catalogue entry the engine already implements. The model chooses; it never
writes code, formulas or numbers.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from functools import cache
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from insightfulcell.engine.types import Meaning

PLAN_LANGUAGE_VERSION = "1"
#: Marks a JSON-schema property whose value must be a column of this table. The gateway turns
#: it into an enum of the real column names, so the model cannot invent one.
COLUMN: dict[str, JsonValue] = {"x-column": True}
COLUMNS: dict[str, JsonValue] = {"items": COLUMN}


class Role(StrEnum):
    MEASURE = "measure"
    DIMENSION = "dimension"
    TIME = "time"
    IDENTIFIER = "identifier"
    TEXT = "text"
    GEOGRAPHY = "geography"
    EXCLUDED = "excluded"


class TargetKind(StrEnum):
    NUMERIC = "numeric"
    BINARY = "binary"
    CATEGORICAL = "categorical"


class CleaningKind(StrEnum):
    NORMALIZE_UNICODE = "normalize_unicode"
    TRIM_WHITESPACE = "trim_whitespace"
    STANDARDIZE_MISSING = "standardize_missing"
    FIX_CATEGORY_CASE = "fix_category_case"
    MERGE_CATEGORIES = "merge_categories"
    PARSE_NUMBERS = "parse_numbers"
    PARSE_PERCENTAGES = "parse_percentages"
    PARSE_DATES = "parse_dates"
    PARSE_BOOLEANS = "parse_booleans"
    SET_INVALID_TO_NULL = "set_invalid_to_null"
    WINSORIZE = "winsorize"
    IMPUTE = "impute"
    DROP_DUPLICATES = "drop_duplicates"
    EXCLUDE_COLUMN = "exclude_column"


class FeatureKind(StrEnum):
    DATE_PART = "date_part"
    DAYS_BETWEEN = "days_between"
    RATIO = "ratio"
    DIFFERENCE = "difference"
    LOG = "log"
    BAND = "band"
    TEXT_LENGTH = "text_length"
    WORD_COUNT = "word_count"
    IS_MISSING = "is_missing"


class DatePart(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEKDAY = "weekday"
    HOUR = "hour"
    IS_WEEKEND = "is_weekend"


class MinerKind(StrEnum):
    """The nine miners (system map: Features and insight mining)."""

    DISTRIBUTION = "distribution"
    RELATIONSHIP = "relationship"
    TREND = "trend"
    SEGMENT = "segment"
    ANOMALY = "anomaly"
    DRIVER = "driver"
    MISSING_AS_SIGNAL = "missing_as_signal"
    BENCHMARK = "benchmark"
    CONDITIONAL = "conditional"


class ChartKind(StrEnum):
    HISTOGRAM = "histogram"
    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    BOX = "box"
    HEATMAP = "heatmap"
    MAP = "map"


class Aggregate(StrEnum):
    COUNT = "count"
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"


class Source(StrEnum):
    RULES = "rules"
    MODEL = "model"
    PERSON = "person"


# --------------------------------------------------------------------------- proposals


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DictionaryProposal(_Proposal):
    column: str = Field(json_schema_extra=COLUMN)
    display_name: str = Field(description="Short human name, no numbers unless in the column name.")
    description: str = Field(description="One sentence: what a value means. No numbers.")
    unit: str | None = Field(
        default=None,
        description="Only if the column name or the values show it (for example $, %, km).",
    )
    meaning: Meaning
    role: Role


class TargetProposal(_Proposal):
    column: str = Field(json_schema_extra=COLUMN)
    kind: TargetKind
    rationale: str = ""


class ValuePair(_Proposal):
    from_value: str = Field(description="A category label exactly as it appears in common_values.")
    to_value: str = Field(description="The label it should become.")


class CleaningProposal(_Proposal):
    kind: CleaningKind
    column: str | None = Field(
        default=None, json_schema_extra=COLUMN, description="Null for drop_duplicates."
    )
    value_map: list[ValuePair] | None = Field(default=None, description="merge_categories only.")
    lower_bound: float | None = Field(default=None, description="set_invalid_to_null only.")
    upper_bound: float | None = Field(default=None, description="set_invalid_to_null only.")
    strategy: Literal["median", "mode"] | None = Field(default=None, description="impute only.")
    reason: str = ""


class FeatureProposal(_Proposal):
    kind: FeatureKind
    columns: list[str] = Field(json_schema_extra=COLUMNS)
    part: DatePart | None = Field(default=None, description="date_part only.")
    bins: int | None = Field(default=None, description="band only, 2 to 10.")
    reason: str = ""


class AnalysisProposal(_Proposal):
    kind: MinerKind
    columns: list[str] = Field(json_schema_extra=COLUMNS)
    question: str = Field(description="The question this analysis answers, in plain words.")
    priority: int = Field(default=2, description="1 (most important) to 3.")


class ChartProposal(_Proposal):
    kind: ChartKind
    x: str = Field(json_schema_extra=COLUMN)
    y: str | None = Field(default=None, json_schema_extra=COLUMN)
    group: str | None = Field(default=None, json_schema_extra=COLUMN)
    aggregate: Aggregate | None = None
    title: str


class RequestStatus(StrEnum):
    """What became of one of the person's requests."""

    ADDED = "added"
    REMOVED = "removed"
    ALREADY_PLANNED = "already_planned"
    NOT_UNDERSTOOD = "not_understood"


class RequestOutcomeProposal(_Proposal):
    index: int = Field(description="The request's number, as given between <requests> tags.")
    status: RequestStatus
    note: str = Field(description="One short sentence for the person. No numbers.")


class RelevanceProposal(_Proposal):
    column: str = Field(json_schema_extra=COLUMN)
    weight: float = Field(description="0 (ignore) to 1 (central to the dataset's purpose).")


class ProposedPlan(_Proposal):
    """What a planner returns: the model through the gateway, or the rule-based planner."""

    dataset_summary: str = Field(
        default="", description="Two sentences: what one row is and what the table is for."
    )
    domain: str = Field(default="", description="A few words, e.g. 'subscription retention'.")
    grain: str = Field(default="", description="What one row represents.")
    dictionary: list[DictionaryProposal] = Field(default_factory=list)
    target: TargetProposal | None = None
    cleaning: list[CleaningProposal] = Field(default_factory=list)
    features: list[FeatureProposal] = Field(default_factory=list)
    analyses: list[AnalysisProposal] = Field(default_factory=list)
    charts: list[ChartProposal] = Field(default_factory=list)
    relevance: list[RelevanceProposal] = Field(default_factory=list)
    #: One per request of the person's, when there were requests (the model fills it).
    request_outcomes: list[RequestOutcomeProposal] = Field(default_factory=list)
    #: Items of the answer that did not fit and were left out, per list (never sent to or
    #: asked of the model, never stored).
    dropped: SkipJsonSchema[dict[str, int]] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _keep_what_fits(cls, data: Any) -> Any:
        """Drop the list items (and the target) that do not fit, instead of the whole plan."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        dropped: dict[str, int] = {}
        for name, item_type in _ITEM_TYPES.items():
            items = out.get(name)
            if not isinstance(items, list):
                continue
            kept = [item for item in items if _fits(item_type, item)]
            if len(kept) != len(items):
                dropped[name] = len(items) - len(kept)
                out[name] = kept
        target = out.get("target")
        if target is not None and not _fits(TargetProposal, target):
            dropped["target"] = 1
            out["target"] = None
        out["dropped"] = dropped
        return out


_ITEM_TYPES: dict[str, type[BaseModel]] = {
    "dictionary": DictionaryProposal,
    "cleaning": CleaningProposal,
    "features": FeatureProposal,
    "analyses": AnalysisProposal,
    "charts": ChartProposal,
    "relevance": RelevanceProposal,
    "request_outcomes": RequestOutcomeProposal,
}


@cache
def _adapter(item_type: type[BaseModel]) -> TypeAdapter[BaseModel]:
    return TypeAdapter(item_type)


def _fits(item_type: type[BaseModel], item: Any) -> bool:
    try:
        _adapter(item_type).validate_python(item)
    except ValidationError:
        return False
    return True


# --------------------------------------------------------------------------- resolved


class _Resolved(BaseModel):
    model_config = ConfigDict(frozen=True)


class ResolvedColumn(_Resolved):
    column: str
    display_name: str
    description: str
    unit: str | None
    meaning: Meaning
    role: Role
    personal_data: str | None = None
    source: Source


class ResolvedTarget(_Resolved):
    column: str
    kind: TargetKind
    rationale: str = ""
    source: Source


class ResolvedFeature(_Resolved):
    id: str
    kind: FeatureKind
    columns: tuple[str, ...]
    part: DatePart | None = None
    bins: int | None = None
    name: str  # the output column name
    reason: str
    source: Source
    enabled: bool = True


class ResolvedAnalysis(_Resolved):
    id: str
    kind: MinerKind
    columns: tuple[str, ...]
    question: str
    priority: int
    source: Source


class ResolvedChart(_Resolved):
    id: str
    kind: ChartKind
    x: str
    y: str | None = None
    group: str | None = None
    aggregate: Aggregate | None = None
    title: str
    source: Source


class RequestOutcome(_Resolved):
    """One of the person's requests and what the planner did with it, in words."""

    request: str
    status: RequestStatus
    note: str
    source: Literal["model", "rules"]


class Rejection(_Resolved):
    section: str
    item: str  # a short description of the rejected item (never a value from the data)
    reason: str


class Provenance(_Resolved):
    source: Literal["model", "cache", "rules"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    fallback_reason: str | None = None
    schema_fingerprint: str
    rejected: tuple[Rejection, ...] = ()


class ResolvedPlan(_Resolved):
    language_version: Literal["1"] = "1"
    dataset_summary: str
    domain: str
    grain: str
    dictionary: tuple[ResolvedColumn, ...]
    target: ResolvedTarget | None
    features: tuple[ResolvedFeature, ...]
    analyses: tuple[ResolvedAnalysis, ...]
    charts: tuple[ResolvedChart, ...]
    relevance: dict[str, float]
    #: Validated cleaning proposals; the clean_plan stage turns them into operations.
    cleaning: tuple[CleaningProposal, ...]
    provenance: Provenance
    #: The person's requests in their own words and what was done with each.
    requests: tuple[RequestOutcome, ...] = ()
    #: Analyses the person removed, kept so the review can offer them back.
    removed_analyses: tuple[ResolvedAnalysis, ...] = ()

    def column(self, name: str) -> ResolvedColumn | None:
        return next((c for c in self.dictionary if c.column == name), None)

    def digest(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def content_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def feature_id(kind: FeatureKind, columns: tuple[str, ...], part: DatePart | None) -> str:
    """Stable across runs: the same feature of the same columns has the same id."""
    return ":".join([kind.value, ",".join(columns), part.value if part else ""]).rstrip(":")
