"""Item-by-item validation of a proposed plan.

Every item a planner proposes (model or rules) passes the same checks. A rejected item is
dropped with a reason a person can read; the rest of the plan survives. Checks:

- columns exist, and the item does not touch personal data (except to describe or exclude it);
- meanings, roles and chart axes fit the column's detected meaning;
- a unit appears only if the column name or its values show it;
- free text carries no numbers except those in column names (numbers come only from facts);
- no feature is derived from the target; section limits hold.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from insightfulcell.engine.features import CATALOGUE as FEATURES
from insightfulcell.engine.planning.language import (
    AnalysisProposal,
    ChartProposal,
    CleaningKind,
    CleaningProposal,
    DatePart,
    DictionaryProposal,
    FeatureKind,
    FeatureProposal,
    MinerKind,
    ProposedPlan,
    Rejection,
    RelevanceProposal,
    Role,
    TargetKind,
    TargetProposal,
)
from insightfulcell.engine.profiling import ColumnProfile, Profile
from insightfulcell.engine.semantics import ColumnSemantics
from insightfulcell.engine.types import CATEGORICAL_MEANINGS, NUMERIC_MEANINGS, Meaning
from insightfulcell.engine.visualization import CATALOGUE as CHARTS

LIMITS: Final = {"cleaning": 40, "features": 30, "analyses": 40, "charts": 12}
TEXT_LIMITS: Final = {
    "summary": 400,
    "domain": 60,
    "grain": 160,
    "display_name": 60,
    "description": 200,
    "reason": 200,
    "question": 200,
    "title": 80,
    "unit": 16,
}
#: Cleaning the planner may propose. Trimming, parsing and the like are decided from the data.
PLANNER_CLEANING: Final = frozenset(
    {
        CleaningKind.MERGE_CATEGORIES,
        CleaningKind.SET_INVALID_TO_NULL,
        CleaningKind.WINSORIZE,
        CleaningKind.IMPUTE,
        CleaningKind.EXCLUDE_COLUMN,
    }
)
COMPATIBLE: Final[dict[Meaning, frozenset[Meaning]]] = {
    Meaning.CONTINUOUS: frozenset({Meaning.CONTINUOUS, Meaning.CURRENCY, Meaning.PERCENTAGE}),
    Meaning.CURRENCY: frozenset({Meaning.CURRENCY, Meaning.CONTINUOUS}),
    Meaning.PERCENTAGE: frozenset({Meaning.PERCENTAGE, Meaning.CONTINUOUS}),
    Meaning.NOMINAL: frozenset(
        {Meaning.NOMINAL, Meaning.GEOGRAPHY, Meaning.IDENTIFIER, Meaning.TEXT}
    ),
    Meaning.GEOGRAPHY: frozenset({Meaning.GEOGRAPHY, Meaning.NOMINAL, Meaning.IDENTIFIER}),
    Meaning.IDENTIFIER: frozenset({Meaning.IDENTIFIER, Meaning.NOMINAL}),
    Meaning.TEXT: frozenset({Meaning.TEXT, Meaning.NOMINAL}),
    Meaning.DATETIME: frozenset({Meaning.DATETIME}),
}
ROLE_FITS: Final[dict[Role, frozenset[Meaning]]] = {
    Role.MEASURE: NUMERIC_MEANINGS,
    Role.DIMENSION: CATEGORICAL_MEANINGS | NUMERIC_MEANINGS,
    Role.TIME: frozenset({Meaning.DATETIME}),
    Role.IDENTIFIER: frozenset({Meaning.IDENTIFIER, Meaning.NOMINAL}),
    Role.TEXT: frozenset({Meaning.TEXT, Meaning.NOMINAL}),
    Role.GEOGRAPHY: frozenset({Meaning.GEOGRAPHY, Meaning.NOMINAL}),
    Role.EXCLUDED: frozenset(Meaning),
}
_DIGITS: Final = re.compile(r"\d+(?:[.,]\d+)*")
_SPACES: Final = re.compile(r"\s+")


@dataclass
class Context:
    semantics: Mapping[str, ColumnSemantics]
    profiles: Mapping[str, ColumnProfile]
    dataset_name: str
    rejected: list[Rejection] = field(default_factory=list)

    def reject(self, section: str, item: str, reason: str) -> None:
        self.rejected.append(Rejection(section=section, item=item[:160], reason=reason))

    def meaning(self, column: str) -> Meaning:
        return self.semantics[column].meaning

    def personal(self, column: str) -> bool:
        return self.semantics[column].personal_data is not None

    def allowed_numbers(self, columns: Iterable[str]) -> set[str]:
        names = [*columns, self.dataset_name]
        return {m.group(0) for name in names for m in _DIGITS.finditer(name)}


def tidy(text: str | None, limit: int) -> str:
    """Printable, single-spaced, NFC, length-limited."""
    if not text:
        return ""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in unicodedata.normalize("NFC", text))
    cleaned = _SPACES.sub(" ", cleaned).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def numbers_ok(text: str, allowed: set[str]) -> bool:
    return all(m.group(0) in allowed for m in _DIGITS.finditer(text))


def validate(
    plan: ProposedPlan,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    *,
    dataset_name: str,
) -> tuple[ProposedPlan, list[Rejection]]:
    ctx = Context(
        semantics={s.name: s for s in semantics},
        profiles={p.name: p for p in profile.columns},
        dataset_name=dataset_name,
    )
    every_column = list(ctx.semantics)
    allowed_all = ctx.allowed_numbers(every_column)
    summary = tidy(plan.dataset_summary, TEXT_LIMITS["summary"])
    if summary and not numbers_ok(summary, allowed_all):
        ctx.reject("summary", "dataset summary", "Contains a number; numbers come only from facts.")
        summary = ""
    domain = tidy(plan.domain, TEXT_LIMITS["domain"])
    grain = tidy(plan.grain, TEXT_LIMITS["grain"])
    if not numbers_ok(domain + " " + grain, allowed_all):
        ctx.reject("summary", "domain or grain", "Contains a number; numbers come only from facts.")
        domain = grain = ""

    dictionary = _dictionary(ctx, plan.dictionary)
    target = _target(ctx, plan.target)
    target_column = target.column if target else None
    cleaning = _limited(
        ctx, "cleaning", [c for c in (_cleaning(ctx, i) for i in plan.cleaning) if c]
    )
    features = _limited(
        ctx, "features", [f for f in (_feature(ctx, i, target_column) for i in plan.features) if f]
    )
    analyses = _limited(
        ctx, "analyses", [a for a in (_analysis(ctx, i, target_column) for i in plan.analyses) if a]
    )
    charts = _limited(ctx, "charts", [c for c in (_chart(ctx, i) for i in plan.charts) if c])
    relevance = [r for r in (_relevance(ctx, i) for i in plan.relevance) if r]
    accepted = ProposedPlan(
        dataset_summary=summary,
        domain=domain,
        grain=grain,
        dictionary=dictionary,
        target=target,
        cleaning=cleaning,
        features=features,
        analyses=analyses,
        charts=charts,
        relevance=relevance,
    )
    return accepted, ctx.rejected


def _limited[T](ctx: Context, section: str, items: list[T]) -> list[T]:
    limit = LIMITS[section]
    if len(items) > limit:
        ctx.reject(section, f"{len(items) - limit} more", f"At most {limit} items are kept.")
    return items[:limit]


def _exists(ctx: Context, section: str, column: str | None, what: str) -> bool:
    if column is None or column not in ctx.semantics:
        ctx.reject(section, what, "Names a column that is not in this table.")
        return False
    return True


# --------------------------------------------------------------------------- sections


def _dictionary(ctx: Context, items: Sequence[DictionaryProposal]) -> list[DictionaryProposal]:
    out: dict[str, DictionaryProposal] = {}
    for item in items:
        what = f"dictionary entry for {item.column}"
        if not _exists(ctx, "dictionary", item.column, what) or item.column in out:
            continue
        sem = ctx.semantics[item.column]
        allowed = ctx.allowed_numbers([item.column])
        display = tidy(item.display_name, TEXT_LIMITS["display_name"])
        if not display or not numbers_ok(display, allowed):
            if display:
                ctx.reject("dictionary", f"display name of {item.column}", "Contains a number.")
            display = ""
        description = tidy(item.description, TEXT_LIMITS["description"])
        if description and not numbers_ok(description, allowed):
            ctx.reject("dictionary", f"description of {item.column}", "Contains a number.")
            description = ""
        unit = tidy(item.unit, TEXT_LIMITS["unit"]) or None
        if unit and not _unit_shown(unit, sem, ctx.profiles[item.column]):
            ctx.reject(
                "dictionary",
                f"unit of {item.column}",
                "The unit is not shown by the column name or its values.",
            )
            unit = sem.unit
        meaning = item.meaning
        if sem.personal_data is not None or meaning not in COMPATIBLE[sem.meaning]:
            if meaning is not sem.meaning:
                ctx.reject(
                    "dictionary",
                    f"meaning of {item.column}",
                    f"The data reads as {sem.meaning.value}, not {meaning.value}.",
                )
            meaning = sem.meaning
        role = item.role
        if sem.personal_data is not None:
            role = Role.EXCLUDED
        elif meaning not in ROLE_FITS[role]:
            ctx.reject(
                "dictionary",
                f"role of {item.column}",
                f"A {meaning.value} column cannot be a {role.value}.",
            )
            role = default_role(meaning)
        out[item.column] = DictionaryProposal(
            column=item.column,
            display_name=display,
            description=description,
            unit=unit,
            meaning=meaning,
            role=role,
        )
    return list(out.values())


def default_role(meaning: Meaning) -> Role:
    return {
        Meaning.CONTINUOUS: Role.MEASURE,
        Meaning.CURRENCY: Role.MEASURE,
        Meaning.PERCENTAGE: Role.MEASURE,
        Meaning.NOMINAL: Role.DIMENSION,
        Meaning.GEOGRAPHY: Role.GEOGRAPHY,
        Meaning.DATETIME: Role.TIME,
        Meaning.IDENTIFIER: Role.IDENTIFIER,
        Meaning.TEXT: Role.TEXT,
    }[meaning]


def _unit_shown(unit: str, sem: ColumnSemantics, profile: ColumnProfile) -> bool:
    folded = unit.casefold()
    if sem.unit and sem.unit.casefold() == folded:
        return True
    if folded in sem.name.casefold():
        return True
    if folded == "%" and sem.meaning is Meaning.PERCENTAGE:
        return True
    shapes = profile.categorical.shapes if profile.categorical else ()
    return any(unit in s.shape for s in shapes)


def _target(ctx: Context, item: TargetProposal | None) -> TargetProposal | None:  # noqa: PLR0911
    if item is None:
        return None
    what = f"target {item.column}"
    if not _exists(ctx, "target", item.column, what):
        return None
    sem, prof = ctx.semantics[item.column], ctx.profiles[item.column]
    if sem.personal_data is not None:
        ctx.reject("target", what, "Personal data cannot be a target.")
        return None
    if sem.meaning in {Meaning.IDENTIFIER, Meaning.TEXT, Meaning.DATETIME}:
        ctx.reject("target", what, f"A {sem.meaning.value} column cannot be a target.")
        return None
    kind = item.kind
    if kind is TargetKind.BINARY and prof.distinct != 2:
        ctx.reject("target", what, "A yes/no target needs exactly two values.")
        return None
    if kind is TargetKind.NUMERIC and sem.meaning not in NUMERIC_MEANINGS:
        ctx.reject("target", what, "A numeric target needs a numeric column.")
        return None
    if kind is TargetKind.CATEGORICAL and (
        sem.meaning not in CATEGORICAL_MEANINGS or prof.distinct > 20
    ):
        ctx.reject("target", what, "A categorical target needs a column with at most 20 groups.")
        return None
    rationale = tidy(item.rationale, TEXT_LIMITS["reason"])
    if not numbers_ok(rationale, ctx.allowed_numbers([item.column])):
        rationale = ""
    return TargetProposal(column=item.column, kind=kind, rationale=rationale)


def _reason(ctx: Context, text: str, columns: Iterable[str]) -> str:
    reason = tidy(text, TEXT_LIMITS["reason"])
    return reason if numbers_ok(reason, ctx.allowed_numbers(columns)) else ""


def _cleaning(ctx: Context, item: CleaningProposal) -> CleaningProposal | None:  # noqa: PLR0911
    if item.kind not in PLANNER_CLEANING:
        return None  # decided from the data; a planner duplicate is not an error
    what = f"{item.kind.value} on {item.column}"
    if not _exists(ctx, "cleaning", item.column, what):
        return None
    assert item.column is not None  # noqa: S101  (_exists checked it)
    meaning = ctx.meaning(item.column)
    if ctx.personal(item.column) and item.kind is not CleaningKind.EXCLUDE_COLUMN:
        ctx.reject("cleaning", what, "Personal data is excluded, not cleaned.")
        return None
    base: dict[str, object] = {
        "kind": item.kind,
        "column": item.column,
        "value_map": None,
        "lower_bound": None,
        "upper_bound": None,
        "strategy": None,
        "reason": _reason(ctx, item.reason, [item.column]),
    }
    if item.kind is CleaningKind.MERGE_CATEGORIES:
        if meaning not in CATEGORICAL_MEANINGS:
            ctx.reject("cleaning", what, "Only categories can be merged.")
            return None
        pairs = [
            p.model_copy(update={"from_value": p.from_value[:80], "to_value": tidy(p.to_value, 80)})
            for p in (item.value_map or [])
            if p.from_value and p.to_value and p.from_value != p.to_value
        ][:50]
        if not pairs:
            ctx.reject("cleaning", what, "No labels to merge.")
            return None
        return CleaningProposal(**{**base, "value_map": pairs})
    if item.kind is CleaningKind.SET_INVALID_TO_NULL:
        bounds = [b for b in (item.lower_bound, item.upper_bound) if b is not None]
        if (
            meaning not in NUMERIC_MEANINGS
            or not bounds
            or not all(math.isfinite(b) for b in bounds)
        ):
            ctx.reject("cleaning", what, "Needs a numeric column and finite bounds.")
            return None
        if (
            item.lower_bound is not None
            and item.upper_bound is not None
            and item.lower_bound >= item.upper_bound
        ):
            ctx.reject("cleaning", what, "The lower bound must be below the upper bound.")
            return None
        return CleaningProposal(
            **{**base, "lower_bound": item.lower_bound, "upper_bound": item.upper_bound}
        )
    if item.kind is CleaningKind.WINSORIZE and meaning not in NUMERIC_MEANINGS:
        ctx.reject("cleaning", what, "Only numbers can be capped.")
        return None
    if item.kind is CleaningKind.IMPUTE:
        if meaning not in NUMERIC_MEANINGS | CATEGORICAL_MEANINGS:
            ctx.reject("cleaning", what, "Only numbers and categories can be filled.")
            return None
        return CleaningProposal(**{**base, "strategy": item.strategy})
    return CleaningProposal(**base)


def _feature(ctx: Context, item: FeatureProposal, target: str | None) -> FeatureProposal | None:
    what = f"{item.kind.value} of {', '.join(item.columns)}"
    spec = FEATURES[item.kind]
    columns = list(dict.fromkeys(item.columns))
    if len(columns) != spec.arity or not all(c in ctx.semantics for c in columns):
        ctx.reject("features", what, f"Takes exactly {spec.arity} existing column(s).")
        return None
    if any(ctx.personal(c) for c in columns):
        ctx.reject("features", what, "Personal data is not used in features.")
        return None
    if target is not None and target in columns:
        ctx.reject("features", what, "A feature cannot be derived from the target.")
        return None
    if not all(ctx.meaning(c) in spec.inputs for c in columns):
        ctx.reject("features", what, "The columns' meanings do not fit this feature.")
        return None
    part = item.part if item.kind is FeatureKind.DATE_PART else None
    if item.kind is FeatureKind.DATE_PART and part is None:
        part = DatePart.MONTH
    bins = max(2, min(10, item.bins or 4)) if item.kind is FeatureKind.BAND else None
    if item.kind is FeatureKind.LOG:
        numeric = ctx.profiles[columns[0]].numeric
        if numeric is None or numeric.p01 is None or numeric.p01 < 0:
            ctx.reject("features", what, "A logarithm needs values that are not negative.")
            return None
    return FeatureProposal(
        kind=item.kind, columns=columns, part=part, bins=bins,
        reason=_reason(ctx, item.reason, columns),
    )  # fmt: skip


_TWO: Final = {
    MinerKind.RELATIONSHIP,
    MinerKind.TREND,
    MinerKind.SEGMENT,
    MinerKind.BENCHMARK,
}


def _analysis(ctx: Context, item: AnalysisProposal, target: str | None) -> AnalysisProposal | None:  # noqa: PLR0912
    columns = list(dict.fromkeys(item.columns))
    what = f"{item.kind.value} of {', '.join(columns)}"
    if not columns or not all(c in ctx.semantics for c in columns):
        ctx.reject("analyses", what, "Names a column that is not in this table.")
        return None
    if any(ctx.personal(c) for c in columns):
        ctx.reject("analyses", what, "Personal data is not analysed.")
        return None
    meanings = [ctx.meaning(c) for c in columns]
    if any(m in {Meaning.IDENTIFIER} for m in meanings):
        ctx.reject("analyses", what, "Identifiers name rows; they are not analysed.")
        return None
    kind = item.kind
    numeric = [c for c, m in zip(columns, meanings, strict=True) if m in NUMERIC_MEANINGS]
    categorical = [c for c, m in zip(columns, meanings, strict=True) if m in CATEGORICAL_MEANINGS]
    dates = [c for c, m in zip(columns, meanings, strict=True) if m is Meaning.DATETIME]
    ok = True
    if kind is MinerKind.DISTRIBUTION:
        ok = len(columns) == 1 and meanings[0] is not Meaning.TEXT
    elif kind in _TWO and len(columns) != 2:
        ok = False
    elif kind is MinerKind.TREND:
        ok = len(dates) == 1 and len(numeric) == 1
    elif kind in {MinerKind.SEGMENT, MinerKind.BENCHMARK}:
        ok = len(categorical) >= 1 and len(numeric) + len(categorical) == 2
    elif kind is MinerKind.RELATIONSHIP:
        ok = Meaning.TEXT not in meanings
    elif kind is MinerKind.ANOMALY:
        ok = 1 <= len(columns) <= 3 and len(numeric) == len(columns)
    elif kind is MinerKind.DRIVER:
        ok = target is not None
        columns = [target] if target is not None else columns
    elif kind is MinerKind.MISSING_AS_SIGNAL:
        ok = len(columns) <= 2 and ctx.profiles[columns[0]].missing > 0
    elif kind is MinerKind.CONDITIONAL:
        ok = len(columns) == 3 and len(categorical) >= 1 and Meaning.TEXT not in meanings
    if not ok:
        ctx.reject("analyses", what, "The columns do not fit this kind of analysis.")
        return None
    question = tidy(item.question, TEXT_LIMITS["question"])
    if not question or not numbers_ok(question, ctx.allowed_numbers(columns)):
        ctx.reject("analyses", what, "The question is missing or contains a number.")
        return None
    return AnalysisProposal(
        kind=kind, columns=columns, question=question, priority=max(1, min(3, item.priority))
    )


def _chart(ctx: Context, item: ChartProposal) -> ChartProposal | None:  # noqa: PLR0911
    what = f"{item.kind.value} of {item.x}" + (f" and {item.y}" if item.y else "")
    spec = CHARTS[item.kind]
    used = [c for c in (item.x, item.y, item.group) if c]
    if not all(c in ctx.semantics for c in used):
        ctx.reject("charts", what, "Names a column that is not in this table.")
        return None
    if any(ctx.personal(c) for c in used):
        ctx.reject("charts", what, "Personal data is never drawn.")
        return None
    if ctx.meaning(item.x) not in spec.x:
        ctx.reject("charts", what, f"A {spec.label.lower()} cannot use this column on its x axis.")
        return None
    y = item.y if spec.y is not None else None
    if spec.y_required and y is None:
        ctx.reject("charts", what, f"A {spec.label.lower()} needs a second column.")
        return None
    if y is not None and spec.y is not None and ctx.meaning(y) not in spec.y:
        ctx.reject("charts", what, f"A {spec.label.lower()} cannot use this column on its y axis.")
        return None
    group = item.group
    if group is not None and (
        ctx.meaning(group) not in CATEGORICAL_MEANINGS or ctx.profiles[group].distinct > 12
    ):
        group = None  # a colour per group only for a handful of groups
    title = tidy(item.title, TEXT_LIMITS["title"])
    if not title or not numbers_ok(title, ctx.allowed_numbers(used)):
        ctx.reject("charts", what, "The title is missing or contains a number.")
        return None
    return ChartProposal(
        kind=item.kind, x=item.x, y=y, group=group, aggregate=item.aggregate, title=title
    )


def _relevance(ctx: Context, item: RelevanceProposal) -> RelevanceProposal | None:
    if item.column not in ctx.semantics or ctx.personal(item.column):
        return None
    weight = item.weight if math.isfinite(item.weight) else 0.5
    return RelevanceProposal(column=item.column, weight=max(0.0, min(1.0, weight)))
