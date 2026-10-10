"""Merge the rule plan, the model plan and a person's saved choices into the resolved plan.

Precedence, per item: what a person confirmed for this dataset, then the model's validated
proposal, then the rules. The model edits the engine's draft and returns a complete plan, so
its features, analyses and charts replace the rules' (a section it left empty falls back to
the rules); dictionary entries and the target are merged item by item. Items that touch an
excluded column, or derive a feature from the target, are dropped. Each item records its
source.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.engine.features import output_name
from insightfulcell.engine.planning.language import (
    AnalysisProposal,
    ChartProposal,
    DictionaryProposal,
    FeatureProposal,
    MinerKind,
    ProposedPlan,
    Provenance,
    ResolvedAnalysis,
    ResolvedChart,
    ResolvedColumn,
    ResolvedFeature,
    ResolvedPlan,
    ResolvedTarget,
    Role,
    Source,
    TargetKind,
    TargetProposal,
    feature_id,
)
from insightfulcell.engine.planning.validator import default_role
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.semantics import ColumnSemantics
from insightfulcell.engine.types import NUMERIC_MEANINGS

MAX_FEATURES: Final = 30
MAX_ANALYSES: Final = 40
MAX_CHARTS: Final = 12


class ColumnAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    display_name: str | None = None
    unit: str | None = None
    excluded: bool | None = None  # None: no choice made; False: include it after all


class Annotations(BaseModel):
    """A person's choices for a dataset, carried into every later run of it."""

    model_config = ConfigDict(frozen=True)

    columns: dict[str, ColumnAnnotation] = Field(default_factory=dict)
    target: str | None = None
    no_target: bool = False  # the person said this dataset has no target
    disabled_features: tuple[str, ...] = ()
    #: Planned analyses the person does not want (analysis ids).
    removed_analyses: tuple[str, ...] = ()
    #: What the person asked for in their own words ("compare hours across services").
    requests: tuple[str, ...] = ()


def resolve(  # noqa: PLR0913  (keyword-only)
    *,
    rules: ProposedPlan,
    model: ProposedPlan | None,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    annotations: Annotations,
    provenance: Provenance,
) -> ResolvedPlan:
    by_name = {s.name: s for s in semantics}
    dictionary = _dictionary(rules, model, semantics, annotations)
    excluded = {c.column for c in dictionary if c.role is Role.EXCLUDED}
    target = _target(rules, model, by_name, profile, annotations, excluded)
    target_name = target.column if target else None
    blocked = excluded | ({target_name} if target_name else set())

    features = _features(
        (model.features if model and model.features else rules.features),
        blocked,
        annotations,
        model,
    )
    analyses = _analyses(
        (model.analyses if model and model.analyses else rules.analyses),
        excluded,
        target_name,
        model,
    )
    charts = _charts((model.charts if model and model.charts else rules.charts), excluded, model)
    relevance = {r.column: r.weight for r in rules.relevance}
    if model:
        relevance.update({r.column: r.weight for r in model.relevance})
    for column in excluded:
        relevance[column] = 0.0
    if target_name:
        relevance[target_name] = 1.0

    def pick(model_text: str, rules_text: str) -> str:
        return model_text or rules_text

    return ResolvedPlan(
        dataset_summary=pick(model.dataset_summary if model else "", rules.dataset_summary),
        domain=pick(model.domain if model else "", rules.domain),
        grain=pick(model.grain if model else "", rules.grain),
        dictionary=tuple(dictionary),
        target=target,
        features=tuple(features),
        analyses=tuple(analyses),
        charts=tuple(charts),
        relevance={k: round(v, 3) for k, v in sorted(relevance.items())},
        cleaning=tuple(model.cleaning if model else ()),
        provenance=provenance,
    )


def _dictionary(
    rules: ProposedPlan,
    model: ProposedPlan | None,
    semantics: Sequence[ColumnSemantics],
    annotations: Annotations,
) -> list[ResolvedColumn]:
    base = {d.column: d for d in rules.dictionary}
    proposed = {d.column: d for d in model.dictionary} if model else {}
    out = []
    for sem in semantics:
        entry: DictionaryProposal = base[sem.name]
        source = Source.RULES
        better = proposed.get(sem.name)
        if better is not None:
            entry = entry.model_copy(
                update={
                    "display_name": better.display_name or entry.display_name,
                    "description": better.description or entry.description,
                    "unit": better.unit if better.unit is not None else entry.unit,
                    "meaning": better.meaning,
                    "role": better.role,
                }
            )
            source = Source.MODEL
        note = annotations.columns.get(sem.name)
        if note is not None:
            updates: dict[str, object] = {}
            if note.display_name:
                updates["display_name"] = note.display_name
            if note.unit is not None:
                updates["unit"] = note.unit or None
            if note.excluded:
                updates["role"] = Role.EXCLUDED
            elif note.excluded is False and entry.role is Role.EXCLUDED:
                updates["role"] = default_role(entry.meaning)
            if updates:
                entry = entry.model_copy(update=updates)
                source = Source.PERSON
        role = Role.EXCLUDED if sem.personal_data is not None else entry.role
        out.append(
            ResolvedColumn(
                column=sem.name,
                display_name=entry.display_name,
                description=entry.description,
                unit=entry.unit,
                meaning=entry.meaning,
                role=role,
                personal_data=sem.personal_data.value if sem.personal_data else None,
                source=source,
            )
        )
    return out


def _target(  # noqa: PLR0913, PLR0917
    rules: ProposedPlan,
    model: ProposedPlan | None,
    semantics: dict[str, ColumnSemantics],
    profile: Profile,
    annotations: Annotations,
    excluded: set[str],
) -> ResolvedTarget | None:
    if annotations.no_target:
        return None
    chosen = annotations.target
    if chosen and chosen in semantics and chosen not in excluded:
        sem = semantics[chosen]
        p = profile.column(chosen)
        if p is not None and p.distinct == 2:
            kind = TargetKind.BINARY
        elif sem.meaning in NUMERIC_MEANINGS:
            kind = TargetKind.NUMERIC
        else:
            kind = TargetKind.CATEGORICAL
        return ResolvedTarget(
            column=chosen, kind=kind, rationale="Chosen by a person.", source=Source.PERSON
        )
    proposal: TargetProposal | None = (model.target if model else None) or rules.target
    if proposal is None or proposal.column in excluded:
        return None
    source = Source.MODEL if model and model.target is proposal else Source.RULES
    return ResolvedTarget(
        column=proposal.column, kind=proposal.kind, rationale=proposal.rationale, source=source
    )


def _features(
    items: Sequence[FeatureProposal],
    blocked: set[str],
    annotations: Annotations,
    model: ProposedPlan | None,
) -> list[ResolvedFeature]:
    seen: dict[str, ResolvedFeature] = {}
    from_model = {id(f) for f in model.features} if model else set()
    for item in items:
        columns = tuple(item.columns)
        if blocked & set(columns):
            continue
        fid = feature_id(item.kind, columns, item.part)
        if fid in seen:
            continue
        seen[fid] = ResolvedFeature(
            id=fid,
            kind=item.kind,
            columns=columns,
            part=item.part,
            bins=item.bins,
            name=output_name(item.kind, columns, item.part),
            reason=item.reason,
            source=Source.MODEL if id(item) in from_model else Source.RULES,
            enabled=fid not in annotations.disabled_features,
        )
    return list(seen.values())[:MAX_FEATURES]


def _analyses(
    items: Sequence[AnalysisProposal],
    excluded: set[str],
    target: str | None,
    model: ProposedPlan | None,
) -> list[ResolvedAnalysis]:
    seen: dict[tuple[str, frozenset[str]], ResolvedAnalysis] = {}
    from_model = {id(a) for a in model.analyses} if model else set()
    for item in items:
        columns = tuple(item.columns)
        if item.kind is MinerKind.DRIVER:
            if target is None:
                continue
            columns = (target,)
        if excluded & set(columns):
            continue
        key = (item.kind.value, frozenset(columns))
        if key in seen:
            continue
        seen[key] = ResolvedAnalysis(
            id=f"{item.kind.value}:{','.join(columns)}",
            kind=item.kind,
            columns=columns,
            question=item.question,
            priority=item.priority,
            source=Source.MODEL if id(item) in from_model else Source.RULES,
        )
    ordered = sorted(seen.values(), key=lambda a: a.priority)  # stable: model first in a tier
    return ordered[:MAX_ANALYSES]


def _charts(
    items: Sequence[ChartProposal], excluded: set[str], model: ProposedPlan | None
) -> list[ResolvedChart]:
    seen: dict[tuple[str, str, str | None, str | None], ResolvedChart] = {}
    from_model = {id(c) for c in model.charts} if model else set()
    for item in items:
        used = {c for c in (item.x, item.y, item.group) if c}
        if excluded & used:
            continue
        key = (item.kind.value, item.x, item.y, item.group)
        if key in seen:
            continue
        seen[key] = ResolvedChart(
            id=":".join([item.kind.value, item.x, item.y or "", item.group or ""]).rstrip(":"),
            kind=item.kind,
            x=item.x,
            y=item.y,
            group=item.group,
            aggregate=item.aggregate,
            title=item.title,
            source=Source.MODEL if id(item) in from_model else Source.RULES,
        )
    return list(seen.values())[:MAX_CHARTS]


def apply_annotations(  # noqa: PLR0912  (one branch per kind of choice)
    plan: ResolvedPlan,
    annotations: Annotations,
    *,
    distinct: dict[str, int],
) -> ResolvedPlan:
    """A person's review choices applied to a resolved plan: names, units, exclusions, the
    target and features switched off. ``distinct`` gives each column's distinct count (for the
    target's kind). Personal data stays excluded whatever is asked."""
    dictionary = []
    for entry in plan.dictionary:
        note = annotations.columns.get(entry.column)
        if note is None:
            dictionary.append(entry)
            continue
        updates: dict[str, object] = {"source": Source.PERSON}
        if note.display_name:
            updates["display_name"] = note.display_name
        if note.unit is not None:
            updates["unit"] = note.unit or None
        if note.excluded or entry.personal_data:
            updates["role"] = Role.EXCLUDED
        elif note.excluded is False and entry.role is Role.EXCLUDED:
            updates["role"] = default_role(entry.meaning)
        dictionary.append(entry.model_copy(update=updates))
    excluded = {c.column for c in dictionary if c.role is Role.EXCLUDED}
    by_name = {c.column: c for c in dictionary}

    target = plan.target
    if annotations.no_target:
        target = None
    elif (
        annotations.target and annotations.target in by_name and annotations.target not in excluded
    ):
        column = by_name[annotations.target]
        if distinct.get(column.column) == 2:
            kind = TargetKind.BINARY
        elif column.meaning in NUMERIC_MEANINGS:
            kind = TargetKind.NUMERIC
        else:
            kind = TargetKind.CATEGORICAL
        target = ResolvedTarget(
            column=column.column, kind=kind, rationale="Chosen by a person.", source=Source.PERSON
        )
    if target is not None and target.column in excluded:
        target = None
    blocked = excluded | ({target.column} if target else set())
    features = tuple(
        f.model_copy(update={"enabled": f.id not in annotations.disabled_features})
        for f in plan.features
        if not blocked & set(f.columns)
    )
    analyses = tuple(
        a
        for a in plan.analyses
        if not excluded & set(a.columns) and (a.kind is not MinerKind.DRIVER or target is not None)
    )
    if target is not None:
        analyses = tuple(
            a.model_copy(update={"columns": (target.column,), "id": f"driver:{target.column}"})
            if a.kind is MinerKind.DRIVER
            else a
            for a in analyses
        )
    charts = tuple(c for c in plan.charts if not excluded & {x for x in (c.x, c.y, c.group) if x})
    relevance = dict(plan.relevance)
    for name in excluded:
        relevance[name] = 0.0
    if target is not None:
        relevance[target.column] = 1.0
    return plan.model_copy(
        update={
            "dictionary": tuple(dictionary),
            "target": target,
            "features": features,
            "analyses": analyses,
            "charts": charts,
            "relevance": relevance,
        }
    )
