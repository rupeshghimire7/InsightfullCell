"""The rule-based planner: the same plan language as the model, from the profile alone.

It is a complete planner on its own (used when the model is off, over budget, failing or
returns an invalid plan) and it fills the gaps in a model's plan. Plainer names and more
generic charts, never wrong ones.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from typing import Final

from insightfulcell.engine.planning.language import (
    Aggregate,
    AnalysisProposal,
    ChartKind,
    ChartProposal,
    DatePart,
    DictionaryProposal,
    FeatureKind,
    FeatureProposal,
    MinerKind,
    ProposedPlan,
    RelevanceProposal,
    Role,
    TargetKind,
    TargetProposal,
)
from insightfulcell.engine.planning.validator import default_role
from insightfulcell.engine.profiling import ColumnProfile, Profile
from insightfulcell.engine.schema_card import TARGET_WORDS
from insightfulcell.engine.semantics import ColumnSemantics, name_tokens
from insightfulcell.engine.types import CATEGORICAL_MEANINGS, NUMERIC_MEANINGS, Meaning

UPPER_WORDS: Final = {
    "id",
    "aud",
    "usd",
    "eur",
    "gbp",
    "nps",
    "gst",
    "ltv",
    "url",
    "ip",
    "sku",
    "kpi",
}
_DESCRIPTION: Final = {
    Meaning.CONTINUOUS: "A measured quantity.",
    Meaning.CURRENCY: "An amount of money.",
    Meaning.PERCENTAGE: "A share, stored as a fraction.",
    Meaning.NOMINAL: "A category.",
    Meaning.GEOGRAPHY: "A place.",
    Meaning.DATETIME: "A date or time.",
    Meaning.IDENTIFIER: "An identifier for a row or a related record.",
    Meaning.TEXT: "Free text.",
}


def humanize(name: str) -> str:
    """``total_spend_aud`` -> ``Total spend AUD``; names with spaces are kept as written."""
    if " " in name.strip():
        return re.sub(r"\s+", " ", name).strip()
    words = list(name_tokens(name)) or [name]
    out = [w.upper() if w in UPPER_WORDS else w for w in words]
    first = out[0]
    out[0] = first if first.isupper() else first[:1].upper() + first[1:]
    return " ".join(out)


class _Columns:
    def __init__(self, semantics: Sequence[ColumnSemantics], profile: Profile) -> None:
        self.semantics = [s for s in semantics if s.personal_data is None]
        self.profiles = {p.name: p for p in profile.columns}

    def usable(self, s: ColumnSemantics) -> bool:
        p = self.profiles[s.name]
        return "constant" not in p.flags and p.missing_share < 0.95

    def of(self, meanings: frozenset[Meaning] | set[Meaning]) -> list[ColumnSemantics]:
        return [s for s in self.semantics if s.meaning in meanings and self.usable(s)]

    def profile(self, s: ColumnSemantics) -> ColumnProfile:
        return self.profiles[s.name]


def plan(semantics: Sequence[ColumnSemantics], profile: Profile) -> ProposedPlan:
    cols = _Columns(semantics, profile)
    target = _target(cols)
    target_name = target.column if target else None
    measures = [s for s in cols.of(NUMERIC_MEANINGS) if s.name != target_name]
    dimensions = [s for s in cols.of(CATEGORICAL_MEANINGS) if 2 <= cols.profile(s).distinct <= 20]
    times = cols.of({Meaning.DATETIME})
    texts = cols.of({Meaning.TEXT})
    names = {s.name: humanize(s.name) for s in semantics}

    return ProposedPlan(
        dataset_summary="",
        domain="",
        grain="",
        dictionary=_dictionary(semantics, profile),
        target=target,
        cleaning=[],
        features=_features(cols, times, measures, texts, target_name),
        analyses=_analyses(cols, target, measures, dimensions, times, names),
        charts=_charts(cols, target, measures, dimensions, times, names),
        relevance=_relevance(semantics, target_name),
    )


def _dictionary(semantics: Sequence[ColumnSemantics], profile: Profile) -> list[DictionaryProposal]:
    profiles = {p.name: p for p in profile.columns}
    out = []
    for s in semantics:
        role = default_role(s.meaning)
        if s.personal_data is not None or "constant" in profiles[s.name].flags:
            role = Role.EXCLUDED
        out.append(
            DictionaryProposal(
                column=s.name,
                display_name=humanize(s.name),
                description=_DESCRIPTION[s.meaning],
                unit=s.unit,
                meaning=s.meaning,
                role=role,
            )
        )
    return out


#: Names that describe a score or rate *about* an outcome, not the outcome itself.
_NOT_OUTCOME: Final = {
    "risk",
    "score",
    "rate",
    "probability",
    "prob",
    "likelihood",
    "date",
    "reason",
}
_GENERIC_TARGET: Final = {"target", "label", "outcome", "y"}


def _target(cols: _Columns) -> TargetProposal | None:
    """Rules propose a target only when the name says outcome and the column is yes/no (or is
    literally called target, label or outcome). A model or a person can propose others."""
    for s in cols.semantics:
        tokens = set(name_tokens(s.name))
        if not tokens & TARGET_WORDS or tokens & _NOT_OUTCOME or not cols.usable(s):
            continue
        p = cols.profile(s)
        if p.distinct == 2:
            return TargetProposal(
                column=s.name,
                kind=TargetKind.BINARY,
                rationale="Named as an outcome, with two values.",
            )
        if s.meaning in NUMERIC_MEANINGS and tokens & _GENERIC_TARGET:
            return TargetProposal(
                column=s.name, kind=TargetKind.NUMERIC, rationale="Named as the target."
            )
    return None


def _features(
    cols: _Columns,
    times: Sequence[ColumnSemantics],
    measures: Sequence[ColumnSemantics],
    texts: Sequence[ColumnSemantics],
    target: str | None,
) -> list[FeatureProposal]:
    out: list[FeatureProposal] = []
    for s in times:
        d = cols.profile(s).datetime
        if d is None or not d.count:
            continue
        span = d.span_days or 0
        if span >= 60 and d.granularity not in {"year", "month"}:
            out.append(
                FeatureProposal(
                    kind=FeatureKind.DATE_PART,
                    columns=[s.name],
                    part=DatePart.WEEKDAY,
                    bins=None,
                    reason="Behaviour often differs by day of the week.",
                )
            )
        if span >= 60:
            out.append(
                FeatureProposal(
                    kind=FeatureKind.DATE_PART,
                    columns=[s.name],
                    part=DatePart.MONTH,
                    bins=None,
                    reason="Seasonality shows by month.",
                )
            )
        if span >= 730:
            out.append(
                FeatureProposal(
                    kind=FeatureKind.DATE_PART,
                    columns=[s.name],
                    part=DatePart.YEAR,
                    bins=None,
                    reason="Change between years.",
                )
            )
    for a, b in itertools.islice(itertools.combinations(times, 2), 2):
        out.append(
            FeatureProposal(
                kind=FeatureKind.DAYS_BETWEEN,
                columns=[a.name, b.name],
                part=None,
                bins=None,
                reason="Time between two events.",
            )
        )
    for s in measures:
        n = cols.profile(s).numeric
        if n and n.skewness and n.skewness > 2 and n.p01 is not None and n.p01 >= 0:
            out.append(
                FeatureProposal(
                    kind=FeatureKind.LOG,
                    columns=[s.name],
                    part=None,
                    bins=None,
                    reason="Strongly right-skewed: a log scale evens it out.",
                )
            )
    for s in texts:
        out.append(
            FeatureProposal(
                kind=FeatureKind.TEXT_LENGTH,
                columns=[s.name],
                part=None,
                bins=None,
                reason="How much was written can carry signal.",
            )
        )
    for s in cols.semantics:
        p = cols.profile(s)
        if (
            s.name != target
            and s.meaning is not Meaning.IDENTIFIER
            and 0.05 <= p.missing_share <= 0.6
        ):
            out.append(
                FeatureProposal(
                    kind=FeatureKind.IS_MISSING,
                    columns=[s.name],
                    part=None,
                    bins=None,
                    reason="Whether a value is missing can itself be informative.",
                )
            )
    return out[:20]


def _analyses(  # noqa: PLR0912, PLR0913, PLR0917  (one rule per miner)
    cols: _Columns,
    target: TargetProposal | None,
    measures: Sequence[ColumnSemantics],
    dimensions: Sequence[ColumnSemantics],
    times: Sequence[ColumnSemantics],
    names: dict[str, str],
) -> list[AnalysisProposal]:
    out: list[AnalysisProposal] = []

    def add(kind: MinerKind, columns: list[str], question: str, priority: int) -> None:
        out.append(
            AnalysisProposal(kind=kind, columns=columns, question=question, priority=priority)
        )

    if target is not None:
        t = names[target.column]
        add(MinerKind.DRIVER, [target.column], f"What is most associated with {t}?", 1)
        for d in dimensions[:4]:
            if d.name != target.column:
                add(
                    MinerKind.SEGMENT,
                    [d.name, target.column],
                    f"Does {t} differ by {names[d.name]}?",
                    1,
                )
        for m in measures[:4]:
            if target.kind is TargetKind.NUMERIC:
                add(
                    MinerKind.RELATIONSHIP,
                    [m.name, target.column],
                    f"Is {names[m.name]} related to {t}?",
                    1,
                )
            else:
                add(
                    MinerKind.SEGMENT,
                    [target.column, m.name],
                    f"Is {names[m.name]} related to {t}?",
                    1,
                )
    for m in measures[:5]:
        add(MinerKind.DISTRIBUTION, [m.name], f"How is {names[m.name]} distributed?", 3)
    for time in times[:2]:
        for m in measures[:3]:
            add(
                MinerKind.TREND,
                [time.name, m.name],
                f"How does {names[m.name]} change over {names[time.name]}?",
                2,
            )
    for d in dimensions[:3]:
        for m in measures[:2]:
            add(
                MinerKind.SEGMENT,
                [d.name, m.name],
                f"Does {names[m.name]} differ by {names[d.name]}?",
                2,
            )
    for d in dimensions[:1]:
        for m in measures[:2]:
            add(
                MinerKind.BENCHMARK,
                [d.name, m.name],
                f"How does each {names[d.name]} compare with the overall {names[m.name]}?",
                3,
            )
    for a, b in itertools.islice(itertools.combinations(measures[:5], 2), 4):
        add(
            MinerKind.RELATIONSHIP,
            [a.name, b.name],
            f"How are {names[a.name]} and {names[b.name]} related?",
            2,
        )
    for m in measures:
        if "outliers" in cols.profile(m).flags:
            add(MinerKind.ANOMALY, [m.name], f"Which rows have unusual {names[m.name]}?", 2)
    for s in cols.semantics:
        p = cols.profile(s)
        if s.meaning is not Meaning.IDENTIFIER and 0.05 <= p.missing_share <= 0.6:
            columns = [s.name, target.column] if target and target.column != s.name else [s.name]
            subject = f" about {names[target.column]}" if target and len(columns) == 2 else ""
            add(
                MinerKind.MISSING_AS_SIGNAL,
                columns,
                f"Does a missing {names[s.name]} say something{subject}?",
                2,
            )
    if target is not None and dimensions and measures:
        d, m = dimensions[0], measures[0]
        if target.column not in {d.name, m.name}:
            add(
                MinerKind.CONDITIONAL,
                [m.name, target.column, d.name],
                f"Does the link between {names[m.name]} and {names[target.column]} "
                f"depend on {names[d.name]}?",
                2,
            )
    return out


def _charts(  # noqa: PLR0913, PLR0917
    cols: _Columns,
    target: TargetProposal | None,
    measures: Sequence[ColumnSemantics],
    dimensions: Sequence[ColumnSemantics],
    times: Sequence[ColumnSemantics],
    names: dict[str, str],
) -> list[ChartProposal]:
    out: list[ChartProposal] = []
    for m in measures[:3]:
        out.append(
            ChartProposal(
                kind=ChartKind.HISTOGRAM,
                x=m.name,
                y=None,
                group=None,
                aggregate=None,
                title=f"Distribution of {names[m.name]}",
            )
        )
    for d in dimensions[:2]:
        out.append(
            ChartProposal(
                kind=ChartKind.BAR,
                x=d.name,
                y=None,
                group=None,
                aggregate=Aggregate.COUNT,
                title=f"Rows by {names[d.name]}",
            )
        )
    if times and measures:
        t, m = times[0], measures[0]
        out.append(
            ChartProposal(
                kind=ChartKind.LINE,
                x=t.name,
                y=m.name,
                group=None,
                aggregate=Aggregate.MEAN,
                title=f"{names[m.name]} over time",
            )
        )
    elif times:
        out.append(
            ChartProposal(
                kind=ChartKind.LINE,
                x=times[0].name,
                y=None,
                group=None,
                aggregate=Aggregate.COUNT,
                title=f"Rows over {names[times[0].name]}",
            )
        )
    if dimensions and measures:
        d, m = dimensions[0], measures[0]
        out.append(
            ChartProposal(
                kind=ChartKind.BOX,
                x=d.name,
                y=m.name,
                group=None,
                aggregate=None,
                title=f"{names[m.name]} by {names[d.name]}",
            )
        )
    if len(measures) >= 2:
        a, b = measures[0], measures[1]
        group = target.column if target and target.kind is TargetKind.BINARY else None
        out.append(
            ChartProposal(
                kind=ChartKind.SCATTER,
                x=a.name,
                y=b.name,
                group=group,
                aggregate=None,
                title=f"{names[b.name]} against {names[a.name]}",
            )
        )
    return out[:8]


def _relevance(semantics: Sequence[ColumnSemantics], target: str | None) -> list[RelevanceProposal]:
    weights = {
        Meaning.CONTINUOUS: 0.7,
        Meaning.CURRENCY: 0.7,
        Meaning.PERCENTAGE: 0.7,
        Meaning.NOMINAL: 0.6,
        Meaning.DATETIME: 0.6,
        Meaning.GEOGRAPHY: 0.5,
        Meaning.TEXT: 0.3,
        Meaning.IDENTIFIER: 0.0,
    }
    return [
        RelevanceProposal(column=s.name, weight=1.0 if s.name == target else weights[s.meaning])
        for s in semantics
        if s.personal_data is None
    ]
