"""The report: the questions answered in words and charts, with how the analysis got there.

Built at the end of Phase B from the resolved plan (what the table is, the questions, the
person's requests), the cleaning that was applied, the facts and charts, and the verified
narration. Every sentence carries the ids of the facts it rests on; the facts themselves are
stored once, immutably, and the report only references them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from insightfulcell.engine.analysis.facts import ChartData, Fact
from insightfulcell.engine.analysis.miners import Finding
from insightfulcell.engine.analysis.narrate import Narration, Sentence
from insightfulcell.engine.planning.language import (
    MinerKind,
    RequestOutcome,
    ResolvedPlan,
    Role,
)

REPORT_VERSION: Final = 2

KIND_LABELS: Final[dict[MinerKind, str]] = {
    MinerKind.DISTRIBUTION: "Distribution",
    MinerKind.RELATIONSHIP: "Relationship",
    MinerKind.TREND: "Trend over time",
    MinerKind.SEGMENT: "Comparison of groups",
    MinerKind.ANOMALY: "Unusual values",
    MinerKind.DRIVER: "What goes with the outcome",
    MinerKind.MISSING_AS_SIGNAL: "Missing values",
    MinerKind.BENCHMARK: "Against the average",
    MinerKind.CONDITIONAL: "Within groups",
}

METHOD_NOTES: Final = (
    "Every number comes from the cleaned table and is stored as a fact you can inspect; "
    "sentences cite the facts they rest on.",
    "Each comparison and link is a statistical test. Across all the tests of this analysis, "
    "the share of false discoveries is held at 5% (Benjamini-Hochberg); a result that does "
    "not pass is reported as not reliable, never as a finding.",
    "The strongest findings are re-tested on the data with only the cleaning that loses "
    "nothing; a finding that changes there is marked.",
    "Findings describe associations in this data. They do not show what causes what.",
    "Strength words follow fixed thresholds on a 0 to 1 scale: below 0.1 negligible, below "
    "0.3 weak, below 0.5 moderate, otherwise strong.",
    "Groups with fewer than ten rows are not reported on their own; they are pooled as "
    "other groups.",
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class ColumnRef(_Model):
    name: str
    display_name: str


class Understanding(_Model):
    summary: str
    domain: str
    grain: str
    target: str | None
    rows: int
    columns: int
    masked_columns: int
    planned_by: Literal["model", "cache", "rules"]


class CleaningStep(_Model):
    label: str
    column: str | None
    rows: int


class Preparation(_Model):
    rows_before: int
    rows_after: int
    applied: tuple[CleaningStep, ...]
    not_applied: int
    reviewed_by_person: bool


class Section(_Model):
    analysis_id: str
    question: str
    kind: str
    kind_label: str
    requested_by_person: bool
    columns: tuple[ColumnRef, ...]
    answer: tuple[Sentence, ...]
    written_by: Literal["model", "engine"] | None
    chart: ChartData | None
    facts: tuple[str, ...]
    skipped: str | None


class Check(_Model):
    """The sensitivity check of one fact."""

    outcome: Literal["holds", "weakens", "flips", "not_retested", "same_data"]
    note: str


class Evidence(_Model):
    tests: int  # hypothesis tests in the run's false-discovery control
    reliable: int  # how many passed
    alpha: float
    rechecked: int


class FeatureNote(_Model):
    label: str
    outcome: Literal["kept", "no_signal", "redundant", "not_computed", "no_target"]
    redundant_with: str | None = None


class NarrationInfo(_Model):
    source: Literal["model", "engine", "mixed"]
    provider: str | None
    model: str | None
    fallback_reason: str | None
    sentences_kept: int
    sentences_dropped: int


class Report(_Model):
    version: int = REPORT_VERSION
    title: str
    dataset_name: str
    understanding: Understanding
    preparation: Preparation
    requests: tuple[RequestOutcome, ...]
    removed_questions: int
    summary: tuple[Sentence, ...]
    sections: tuple[Section, ...]
    narration: NarrationInfo
    #: Display names of the table's columns, for showing the facts' columns.
    column_names: dict[str, str]
    method_notes: tuple[str, ...] = METHOD_NOTES
    #: The best findings, best first (the summary is written from them).
    highlights: tuple[str, ...] = ()
    checks: dict[str, Check] = {}
    evidence: Evidence | None = None
    features: tuple[FeatureNote, ...] = ()


def build(  # noqa: PLR0913  (keyword-only)
    *,
    dataset_name: str,
    plan: ResolvedPlan,
    findings: Sequence[Finding],
    narration: Narration,
    preparation: Preparation,
    rows: int,
    highlights: Sequence[str] = (),
    checks: Mapping[str, Check] | None = None,
    evidence: Evidence | None = None,
    features: Sequence[FeatureNote] = (),
    derived_names: Mapping[str, str] | None = None,
) -> Report:
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    names.update(derived_names or {})
    sections = []
    for finding in findings:
        a = finding.analysis
        sections.append(
            Section(
                analysis_id=a.id,
                question=a.question,
                kind=a.kind.value,
                kind_label="Places" if a.id.startswith("places:") else KIND_LABELS[a.kind],
                requested_by_person=a.source.value == "person",
                columns=tuple(ColumnRef(name=c, display_name=names.get(c, c)) for c in a.columns),
                answer=narration.sections.get(a.id, ()),
                written_by=narration.written_by.get(a.id),
                chart=finding.chart,
                facts=tuple(f.id for f in finding.facts),
                skipped=finding.skipped,
            )
        )
    # Answered questions first, in plan order (the planners put the important ones first).
    sections.sort(key=lambda s: s.skipped is not None)
    understanding = Understanding(
        summary=plan.dataset_summary,
        domain=plan.domain,
        grain=plan.grain,
        target=names.get(plan.target.column) if plan.target else None,
        rows=rows,
        columns=len(plan.dictionary),
        masked_columns=sum(1 for c in plan.dictionary if c.personal_data),
        planned_by=plan.provenance.source,
    )
    title_name = dataset_name or "your dataset"
    return Report(
        title=f"Report on {title_name}",
        dataset_name=dataset_name,
        understanding=understanding,
        preparation=preparation,
        requests=plan.requests,
        removed_questions=len(plan.removed_analyses),
        summary=narration.summary,
        sections=tuple(sections),
        narration=NarrationInfo(
            source=narration.source,
            provider=narration.provider,
            model=narration.model,
            fallback_reason=narration.fallback_reason,
            sentences_kept=narration.kept,
            sentences_dropped=narration.dropped,
        ),
        column_names=names,
        highlights=tuple(highlights),
        checks=dict(checks or {}),
        evidence=evidence,
        features=tuple(features),
    )


def all_facts(findings: Sequence[Finding]) -> list[Fact]:
    return [f for finding in findings for f in finding.facts]


def analysed_columns(plan: ResolvedPlan) -> list[str]:
    """Columns that take part in the analysis (not excluded, not personal data)."""
    return [c.column for c in plan.dictionary if c.role is not Role.EXCLUDED]


def context(plan: ResolvedPlan, preparation: Preparation) -> Mapping[str, object]:
    """What the narrator is told besides the facts: what the table is and how it was
    prepared, in words and counts (no values)."""
    return {
        "table": plan.dataset_summary,
        "domain": plan.domain,
        "one_row_is": plan.grain,
        "outcome": plan.target.column if plan.target else None,
        "rows_after_cleaning": preparation.rows_after,
        "cleaning_changes_applied": len(preparation.applied),
        "requests_from_the_person": [
            r.request for r in plan.requests if r.status != "not_understood"
        ],
    }
