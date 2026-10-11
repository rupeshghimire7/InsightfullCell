"""The report: the questions answered in words and charts, with how the analysis got there.

Built at the end of Phase B from the resolved plan (what the table is, the questions, the
person's requests), the cleaning that was applied, the facts, the charts and maps that passed
the ``visualize`` check, and the verified narration. Every sentence carries the ids of the facts
it rests on; the facts themselves are stored once, immutably, and the report only references
them. A drill-down's report says which subset it covers and that its findings are exploratory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from insightfulcell.engine.analysis.facts import ChartData, Fact, MapData
from insightfulcell.engine.analysis.miners import Finding
from insightfulcell.engine.analysis.narrate import Narration, Sentence
from insightfulcell.engine.planning.language import (
    MinerKind,
    RequestOutcome,
    ResolvedPlan,
    Role,
)

REPORT_VERSION: Final = 4

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
    "A language model writes the answers with placeholders for numbers; the engine fills in "
    "every number from the facts and checks each sentence (sources, numbers, direction, "
    "strength words, conditions, no claims of cause). A sentence that fails is rewritten once, "
    "then replaced by the fact's own statement.",
    "Strength words follow fixed thresholds on a 0 to 1 scale: below 0.1 negligible, below "
    "0.3 weak, below 0.5 moderate, otherwise strong.",
    "Every value a chart or map shows was computed again from the cleaned data, one value at a "
    "time, before the chart was shown; a chart with any value that did not match is withheld.",
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
    #: Maps of the place this question is about (checked like charts).
    maps: tuple[MapData, ...] = ()
    #: The miner drew a chart, but a value did not match the data, so it is not shown.
    chart_withheld: bool = False


class Scope(_Model):
    """The subset a drill-down analysed, as the person chose it from the parent's report."""

    parent_run_id: str
    column: str
    display_name: str
    values: tuple[str, ...]
    rows: int  # rows of the subset after cleaning


class Visuals(_Model):
    """What the ``visualize`` check did."""

    charts: int  # charts shown, each verified
    withheld: int
    maps: int
    claims: int  # values recomputed


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
    sentences_repaired: int = 0
    #: The model was unavailable for some part, written from the facts' statements instead.
    simplified: bool = False
    #: Narrating again may succeed (the model was out, not switched off or refused).
    retryable: bool = False
    #: Parts written from templates because too many of their sentences failed the checks.
    flagged: tuple[str, ...] = ()
    judge: Literal["off", "on"] = "off"
    #: Rejected sentences per check (first drafts and repairs).
    rejected: dict[str, int] = {}
    prompt_version: str | None = None


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
    #: Set for a drill-down: the subset, and every finding is exploratory.
    scope: Scope | None = None
    visuals: Visuals | None = None


EXPLORATORY_NOTE: Final = (
    "This is an exploratory analysis of a subset chosen after seeing the full report. Its "
    "findings are tested at the same 5% level within this subset, but choosing where to look "
    "after seeing the data makes chance findings more likely: treat them as leads to check, "
    "not conclusions."
)


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
    charts: Mapping[str, ChartData] | None = None,
    withheld: frozenset[str] = frozenset(),
    maps: Mapping[str, Sequence[MapData]] | None = None,
    scope: Scope | None = None,
    visuals: Visuals | None = None,
) -> Report:
    """``charts`` are the verified charts by analysis id (a finding's own chart is used when
    ``charts`` is None, as in tests and the CLI); ``withheld`` the ones that failed."""
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    names.update(derived_names or {})
    sections = []
    for finding in findings:
        a = finding.analysis
        chart = finding.chart if charts is None else charts.get(a.id)
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
                chart=chart,
                facts=tuple(f.id for f in finding.facts),
                skipped=finding.skipped,
                maps=tuple((maps or {}).get(a.id, ())) if finding.skipped is None else (),
                chart_withheld=a.id in withheld,
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
    title = f"Report on {title_name}"
    if scope is not None:
        named = ", ".join(scope.values[:3])
        more = len(scope.values) - 3
        if more > 0:
            named += f" and {more} more"
        title = f"Exploring {scope.display_name}: {named} ({title_name})"
    return Report(
        title=title,
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
            sentences_repaired=narration.repaired,
            simplified=narration.simplified,
            retryable=narration.retryable,
            flagged=narration.flagged,
            judge=narration.judge,
            rejected=narration.rejected,
            prompt_version=narration.prompt_version,
        ),
        column_names=names,
        method_notes=(EXPLORATORY_NOTE, *METHOD_NOTES) if scope is not None else METHOD_NOTES,
        highlights=tuple(highlights),
        checks=dict(checks or {}),
        evidence=evidence,
        features=tuple(features),
        scope=scope,
        visuals=visuals,
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
