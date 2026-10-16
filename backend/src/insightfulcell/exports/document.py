"""The report as a short list of neutral blocks, for the Markdown, Word and PDF exports.

The HTML export renders the stored documents through its template; the other formats are
built from these blocks, so all of them say the same thing in the same order: the scope and
narration notices, the dashboard tiles, the summary, every answer with its chart (an image of
the same SVG) and the numbers behind it, the questions that could not be answered, how the
report was made, and every fact with its test and content hash. Text here is plain (no markup);
each renderer escapes it for its own format.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from insightfulcell import __version__
from insightfulcell.engine.analysis.dashboard import Dashboard
from insightfulcell.engine.analysis.facts import ChartData, MapData
from insightfulcell.engine.analysis.narrate import Sentence
from insightfulcell.engine.analysis.report import Report, Section
from insightfulcell.exports.html import EFFECT_LABELS, TIER_LABELS, writing_note
from insightfulcell.exports.svg import chart_svg, format_value, map_legend, map_svg


@dataclass(frozen=True, slots=True)
class Text:
    """A run of text; ``cite`` marks a source tag such as ``F8``."""

    text: str
    cite: bool = False
    strong: bool = False


@dataclass(frozen=True, slots=True)
class Heading:
    level: int  # 1 (title) to 3
    text: str


@dataclass(frozen=True, slots=True)
class Para:
    parts: tuple[Text, ...]
    small: bool = False


@dataclass(frozen=True, slots=True)
class Note:
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Bullets:
    items: tuple[tuple[Text, ...], ...]
    small: bool = False


@dataclass(frozen=True, slots=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    numeric: tuple[bool, ...]  # right-align these columns


@dataclass(frozen=True, slots=True)
class Figure:
    title: str
    svg: str  # self-contained SVG markup (escaped by ``exports.svg``)
    table: Table
    note: str | None = None
    legend: tuple[str, ...] = ()


Block = Heading | Para | Note | Bullets | Table | Figure


@dataclass(frozen=True, slots=True)
class Document:
    title: str
    meta: str
    blocks: tuple[Block, ...]
    footer: str
    run_id: str
    dataset_name: str


@dataclass(slots=True)
class _Builder:
    blocks: list[Block] = field(default_factory=list)

    def add(self, block: Block) -> None:
        self.blocks.append(block)


def build_document(
    *,
    report: Report,
    dashboard: Dashboard | None,
    facts: Sequence[Mapping[str, Any]],
    run_id: str,
    written_at: datetime,
) -> Document:
    by_id = {str(f["id"]): f for f in facts}
    out = _Builder()

    if report.scope:
        out.add(
            Note(
                "Exploratory analysis.",
                f"Only the rows where {report.scope.display_name} is "
                f"{', '.join(report.scope.values)} ({report.scope.rows:,} rows). These findings "
                "are leads to check, not conclusions.",
            )
        )
    if report.narration.simplified:
        out.add(
            Note(
                "Narration simplified.",
                "The language model was unavailable for some answers, so they are written from "
                "the facts' own statements.",
            )
        )

    if dashboard and dashboard.tiles:
        out.add(Heading(2, "At a glance"))
        out.add(
            Table(
                header=("Measure", "Value", "Question", "Source"),
                rows=tuple((t.label, t.value, t.question, t.fact_id) for t in dashboard.tiles),
                numeric=(False, True, False, False),
            )
        )

    out.add(Heading(2, "In short"))
    if report.summary:
        out.add(Para(_sentences(report.summary)))
    else:
        out.add(Para((Text("None of the questions could be answered on this data."),)))

    answered = [s for s in report.sections if s.skipped is None]
    if answered:
        out.add(Heading(2, "Answers"))
    for section in answered:
        _answer(out, section, by_id)

    skipped = [s for s in report.sections if s.skipped is not None]
    if skipped:
        out.add(Heading(2, "Questions that could not be answered"))
        out.add(
            Bullets(tuple((Text(s.question, strong=True), Text(f" {s.skipped}")) for s in skipped))
        )

    _how(out, report)
    _facts(out, facts)

    return Document(
        title=report.title,
        meta=(
            f"Dataset: {report.dataset_name or 'untitled'} · Written "
            f"{written_at.strftime('%-d %B %Y, %H:%M UTC')} · Run {run_id}"
        ),
        blocks=tuple(out.blocks),
        footer=(
            f"Made with InsightfulCell {__version__}. Raw rows never left your organisation's "
            "storage; language models saw only the facts."
        ),
        run_id=run_id,
        dataset_name=report.dataset_name,
    )


def _answer(out: _Builder, section: Section, by_id: Mapping[str, Mapping[str, Any]]) -> None:
    kind = section.kind_label + (" · You asked for this" if section.requested_by_person else "")
    out.add(Para((Text(kind),), small=True))
    out.add(Heading(3, section.question))
    out.add(Para(_sentences(section.answer)))
    if section.chart:
        out.add(_chart_figure(section.chart))
    if section.chart_withheld:
        withheld = (
            "A chart for this answer was withheld because one of its values did not match the data."
        )
        out.add(Para((Text(withheld),), small=True))
    for region_map in section.maps:
        out.add(_map_figure(region_map))
    sources = [by_id[fid] for fid in section.facts if fid in by_id]
    if sources:
        out.add(
            Bullets(
                tuple(
                    (
                        Text(str(f["id"]), cite=True),
                        Text(f" {f['statement']} "),
                        Text(f"({_tier(f)})"),
                    )
                    for f in sources
                ),
                small=True,
            )
        )


def _facts(out: _Builder, facts: Sequence[Mapping[str, Any]]) -> None:
    out.add(Heading(2, "Facts"))
    out.add(
        Para(
            (
                Text(
                    "Every number in this report comes from one of these facts. Each is stored "
                    "unchanged, with a content hash."
                ),
            ),
            small=True,
        )
    )
    for f in facts:
        _fact(out, f)


def _sentences(items: Sequence[Sentence]) -> tuple[Text, ...]:
    parts: list[Text] = []
    for i, sentence in enumerate(items):
        parts.append(Text(("" if i == 0 else " ") + sentence.text))
        parts.extend(Text(c, cite=True) for c in sentence.citations)
    return tuple(parts)


def _tier(fact: Mapping[str, Any]) -> str:
    return TIER_LABELS.get(str(fact.get("tier")), str(fact.get("tier")))


def _chart_figure(chart: ChartData) -> Figure:
    show_value = chart.kind != "scatter"
    show_rows = any(p.n is not None for p in chart.points)
    header = (
        chart.x_label,
        *((chart.y_label,) if show_value else ()),
        *(("Rows",) if show_rows else ()),
    )
    rows = tuple(
        (
            p.label,
            *((format_value(p.value, chart.value_format, chart.unit),) if show_value else ()),
            *((f"{p.n:,}" if p.n is not None else "",) if show_rows else ()),
        )
        for p in chart.points
    )
    numeric = (False, *((True,) if show_value else ()), *((True,) if show_rows else ()))
    return Figure(
        title=chart.title,
        svg=str(chart_svg(chart)),
        table=Table(header=header, rows=rows, numeric=numeric),
        note=chart.note,
    )


def _map_figure(data: MapData) -> Figure:
    rows = tuple(
        (
            r.name,
            format_value(r.value, data.value_format, data.unit)
            if r.status == "value" and r.value is not None
            else "Fewer than ten rows",
            f"{r.n:,}" if r.n is not None else "",
        )
        for r in data.regions
        if r.status != "no_rows"
    )
    legend = [words for _, words in map_legend(data)]
    if any(r.status == "few_rows" for r in data.regions):
        legend.append("Hatched: fewer than ten rows")
    return Figure(
        title=data.title,
        svg=str(map_svg(data)),
        table=Table(
            header=("Region", data.value_label, "Rows"), rows=rows, numeric=(False, True, True)
        ),
        note=data.note,
        legend=tuple(legend),
    )


def _how(out: _Builder, report: Report) -> None:
    out.add(Heading(2, "How this report was made"))
    u = report.understanding
    out.add(Heading(3, "The table"))
    if u.summary:
        out.add(Para((Text(u.summary),)))
    masked = f", {u.masked_columns:,} masked as personal data" if u.masked_columns else ""
    target = f" Outcome: {u.target}." if u.target else ""
    out.add(
        Para(
            (Text(f"{u.rows:,} rows analysed, {u.columns:,} columns{masked}.{target}"),),
            small=True,
        )
    )

    p = report.preparation
    out.add(Heading(3, "Cleaning"))
    n = len(p.applied)
    how = "after review" if p.reviewed_by_person else "automatically"
    out.add(
        Para(
            (
                Text(
                    f"{n} change{'' if n == 1 else 's'} applied {how}; {p.rows_before:,} rows "
                    f"went in and {p.rows_after:,} were analysed."
                ),
            ),
            small=True,
        )
    )
    if p.applied:
        out.add(
            Bullets(
                tuple((Text(f"{s.label}: {s.column or 'all rows'}"),) for s in p.applied),
                small=True,
            )
        )

    out.add(Heading(3, "Writing"))
    out.add(Para((Text(writing_note(report)),), small=True))

    if report.evidence:
        e = report.evidence
        text = (
            f"{e.tests:,} comparisons and links were tested; {e.reliable:,} are reliable, with "
            f"false discoveries held at {round(e.alpha * 100)}% across the analysis."
        )
        if report.visuals:
            v = report.visuals
            text += (
                f" {v.claims:,} chart and map values were recomputed from the data before being "
                "shown"
            )
            if v.withheld:
                text += f"; {v.withheld:,} chart{'' if v.withheld == 1 else 's'} withheld"
            text += "."
        out.add(Heading(3, "Evidence"))
        out.add(Para((Text(text),), small=True))
    if report.method_notes:
        out.add(Bullets(tuple((Text(note),) for note in report.method_notes), small=True))


def _fact(out: _Builder, f: Mapping[str, Any]) -> None:
    head = [Text(str(f["id"]), cite=True), Text(f" {f['statement']} "), Text(f"({_tier(f)})")]
    if f.get("exploratory"):
        head.append(Text(" (Exploratory)"))
    out.add(Para(tuple(head)))
    columns = ", ".join(str(c.get("display_name", c.get("name", ""))) for c in f.get("columns", []))
    lines = [f"Columns: {columns} · {int(f.get('rows', 0)):,} rows · {f.get('method', '')}"]
    test = f.get("test")
    if test:
        effect = EFFECT_LABELS.get(
            str(test["effect_name"]), str(test["effect_name"]).replace("_", " ")
        )
        interval = (
            f" (95% interval {test['ci_low']:.3g} to {test['ci_high']:.3g})"
            if test.get("ci_low") is not None and test.get("ci_high") is not None
            else ""
        )
        lines.append(
            f"{test['method']}: {effect} {test['effect']:.3g}{interval}; "
            f"p {test['p_value']:.3g}, q {test['q_value']:.3g}."
        )
    check = f.get("check")
    if check and check.get("outcome") != "same_data":
        lines.append(str(check.get("note", "")))
    lines.append(f"Content hash {f.get('content_hash', '')}")
    out.add(Bullets(tuple((Text(line),) for line in lines), small=True))
