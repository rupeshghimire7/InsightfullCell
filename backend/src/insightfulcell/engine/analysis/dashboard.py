"""The dashboard: the report's best findings at a glance.

Assembled from the finished report (so it holds nothing the report does not): headline tiles
(one number each, taken from a highlighted fact as the fact writes it), the charts of the top
findings (answers resting on a reliable test), the maps, a gallery of the other verified
charts, and suggested questions already answered with the report's verified sentences. Every
tile and chart names the facts it rests on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from insightfulcell.engine.analysis.facts import ChartData, Fact, MapData, Tier
from insightfulcell.engine.analysis.narrate import Sentence
from insightfulcell.engine.analysis.rank import QUALITY_KINDS
from insightfulcell.engine.analysis.report import Report, Section

DASHBOARD_VERSION: Final = 1
TILES: Final = 4
INSIGHTS: Final = 4
GALLERY: Final = 8
QUESTIONS: Final = 6
#: Descriptive findings (what a column looks like) go to the gallery, not the top insights.
_DESCRIPTIVE: Final = frozenset(
    {"distribution", "date_range", "category_share", "missing_share", "outliers"}
)
#: Which of a fact's numbers is its headline, and what to call it.
_HEADLINE: Final[dict[str, tuple[int, str]]] = {
    "group_comparison": (0, "Highest group average"),
    "rate_comparison": (0, "Highest group share"),
    "benchmark": (1, "Furthest above average"),
    "correlation": (0, "Rank correlation"),
    "association": (0, "Strength of the link (Cramér's V)"),
    "conditional": (1, "Correlation within groups"),
    "trend": (3, "Change over the period"),
    "distribution": (0, "Typical value (median)"),
    "category_share": (0, "Most common value"),
    "missing_share": (0, "Rows with no value"),
    "missing_effect": (0, "When the value is missing"),
    "outliers": (0, "Rows with unusual values"),
    "driver": (0, "Strength of association"),
    "date_range": (0, "Rows in the busiest period"),
}


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Tile(_Model):
    fact_id: str
    analysis_id: str
    label: str
    value: str  # one of the fact's own formatted numbers
    question: str
    statement: str
    tier: Tier


class Insight(_Model):
    analysis_id: str
    question: str
    kind_label: str
    chart: ChartData | None = None
    map: MapData | None = None
    facts: tuple[str, ...]


class SuggestedQuestion(_Model):
    analysis_id: str
    question: str
    answer: tuple[Sentence, ...]
    written_by: Literal["model", "engine"] | None = None


class Glance(_Model):
    rows: int
    columns: int
    tests: int
    reliable: int
    charts_verified: int
    charts_withheld: int


class Dashboard(_Model):
    version: int = DASHBOARD_VERSION
    title: str
    dataset_name: str
    glance: Glance
    tiles: tuple[Tile, ...]
    insights: tuple[Insight, ...]
    maps: tuple[Insight, ...]
    gallery: tuple[Insight, ...]
    questions: tuple[SuggestedQuestion, ...]
    #: Set for a drill-down: findings are exploratory (see the report's ``scope``).
    exploratory: bool = False


def build(
    report: Report,
    facts: Mapping[str, Fact],
    *,
    charts_withheld: int = 0,
) -> Dashboard:
    answered = [s for s in report.sections if s.skipped is None]
    order = _order(answered, report.highlights, facts)
    tiles = _tiles(report.highlights, facts, {s.analysis_id: s for s in answered})
    charted = [s for s in order if s.chart is not None and s.chart.check is not None]
    # Top findings are findings: a reliable test (strong or supported), not a description and
    # not a difference that did not survive the false-discovery check.
    top = [s for s in charted if _reliable(s, facts)][:INSIGHTS]
    chosen = {s.analysis_id for s in top}
    gallery = [s for s in charted if s.analysis_id not in chosen][:GALLERY]
    maps = [
        Insight(
            analysis_id=s.analysis_id,
            question=s.question,
            kind_label=s.kind_label,
            map=m,
            facts=s.facts,
        )
        for s in answered
        for m in s.maps
    ]
    questions = [
        SuggestedQuestion(
            analysis_id=s.analysis_id,
            question=s.question,
            answer=s.answer,
            written_by=s.written_by,
        )
        for s in order
        if s.answer
    ][:QUESTIONS]
    evidence = report.evidence
    return Dashboard(
        title=(
            f"Dashboard: {report.dataset_name or 'your dataset'}"
            if report.scope is None
            else f"Dashboard: {report.title}"  # "Exploring State: Victoria (sales.csv)"
        ),
        dataset_name=report.dataset_name,
        glance=Glance(
            rows=report.understanding.rows,
            columns=report.understanding.columns,
            tests=evidence.tests if evidence else 0,
            reliable=evidence.reliable if evidence else 0,
            charts_verified=sum(
                1 for s in answered if s.chart is not None and s.chart.check is not None
            )
            + len(maps),
            charts_withheld=charts_withheld,
        ),
        tiles=tiles,
        insights=tuple(_insight(s) for s in top),
        maps=tuple(maps),
        gallery=tuple(_insight(s) for s in gallery),
        questions=tuple(questions),
        exploratory=report.scope is not None,
    )


def _insight(section: Section) -> Insight:
    return Insight(
        analysis_id=section.analysis_id,
        question=section.question,
        kind_label=section.kind_label,
        chart=section.chart,
        facts=section.facts,
    )


def _order(
    answered: Sequence[Section], highlights: Sequence[str], facts: Mapping[str, Fact]
) -> list[Section]:
    """Sections whose facts are highlighted first (in highlight order), then the plan's
    order (the planners put the important questions first)."""
    by_analysis = {s.analysis_id: s for s in answered}
    first: list[Section] = []
    for fact_id in highlights:
        fact = facts.get(fact_id)
        section = by_analysis.get(fact.analysis_id) if fact else None
        if section is not None and section not in first:
            first.append(section)
    return first + [s for s in answered if s not in first]


def _reliable(section: Section, facts: Mapping[str, Fact]) -> bool:
    return any(
        facts[f].tier in {"strong", "supported"}
        and facts[f].kind not in _DESCRIPTIVE | QUALITY_KINDS
        for f in section.facts
        if f in facts
    )


def _tiles(
    highlights: Sequence[str], facts: Mapping[str, Fact], sections: Mapping[str, Section]
) -> tuple[Tile, ...]:
    tiles: list[Tile] = []
    used: set[str] = set()
    for fact_id in highlights:
        fact = facts.get(fact_id)
        if fact is None or fact.kind in QUALITY_KINDS or fact.tier == "inconclusive":
            continue
        section = sections.get(fact.analysis_id)
        if section is None or fact.analysis_id in used:
            continue
        headline = headline_of(fact)
        if headline is None:
            continue
        value, label = headline
        tiles.append(
            Tile(
                fact_id=fact.id,
                analysis_id=fact.analysis_id,
                label=label,
                value=value,
                question=section.question,
                statement=fact.statement,
                tier=fact.tier,
            )
        )
        used.add(fact.analysis_id)
        if len(tiles) == TILES:
            break
    return tuple(tiles)


def headline_of(fact: Fact) -> tuple[str, str] | None:
    """The number a tile shows (one of the fact's own) and its label, naming the group or
    value it belongs to when the fact names one."""
    if not fact.numbers:
        return None
    index, label = _HEADLINE.get(fact.kind, (0, "Key figure"))
    if fact.kind == "trend" and not fact.values.get("first"):
        # No change in percent without a first value: the latest average instead.
        index, label = 1, "Latest average"
    if fact.kind == "outliers" and not fact.values.get("outside"):
        return None  # "no unusual value": its numbers are the range, not a count
    if index >= len(fact.numbers):
        return None
    value = fact.numbers[index]
    named = {"group_comparison", "rate_comparison", "benchmark", "category_share"}
    if fact.kind in named and fact.labels:
        label = f"{label}: {fact.labels[0]}"
    return value, label
