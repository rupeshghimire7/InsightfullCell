"""Ranking: which findings to show first.

A finding's score combines the strength of evidence (its tier and q-value), the size of the
effect, and the relevance of its columns from the plan (the outcome counts most), then is
discounted when it repeats columns already covered by a higher finding (novelty) or when the
sensitivity check found it depends on the cleaning. Exact repeats (the same test, or the same
statement of the same columns) are marked as duplicates and kept out of the highlights.

Ranking changes only what is shown first, never whether a finding is true: tiers and wording
were settled by the false-discovery control before this.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.sensitivity import Recheck
from insightfulcell.engine.planning.language import ResolvedPlan

HIGHLIGHTS: Final = 5
WEIGHTS: Final = {"evidence": 0.4, "size": 0.35, "relevance": 0.25}
STABILITY: Final = {
    "holds": 1.0,
    "same_data": 1.0,
    "not_retested": 0.9,
    "weakens": 0.6,
    "flips": 0.4,
}
NOVELTY: Final = (1.0, 0.75, 0.5)
#: Facts about the data rather than findings in it: shown with their question, not highlighted.
QUALITY_KINDS: Final = frozenset({"place_match", "place_unmatched", "driver_model", "leakage"})
#: A descriptive fact's weight in place of an effect size: summaries of a whole column first,
#: counts of unusual or missing values after.
_DESCRIPTIVE_SIZE: Final = {
    "leakage": 0.9,
    "driver_model": 0.5,
    "place_unmatched": 0.4,
    "distribution": 0.35,
    "category_share": 0.35,
    "date_range": 0.3,
    "outliers": 0.2,
    "missing_share": 0.2,
}
PER_KIND: Final = 2  # highlights of one kind, so the summary covers more than one angle


@dataclass(frozen=True, slots=True)
class Ranked:
    fact_id: str
    score: float
    evidence: float
    size: float
    relevance: float
    novelty: float
    stability: float
    duplicate_of: str | None = None


def evidence(fact: Fact) -> float:
    """0 (no reliable evidence) to 1. Descriptive facts sit in the middle: they are exact, but
    they test nothing."""
    if fact.test is None or fact.tier == "descriptive":
        return 0.4
    if fact.tier == "inconclusive":
        return 0.0
    strength = -math.log10(max(fact.test.q_value, 1e-300))
    if fact.tier == "strong":
        return 0.75 + 0.25 * min(1.0, (strength - 2) / 8)
    return 0.5 + 0.25 * min(1.0, max(0.0, (strength - 1.3) / 0.7))


def rank(
    facts: Sequence[Fact], plan: ResolvedPlan, rechecks: Mapping[str, Recheck]
) -> list[Ranked]:
    """Every fact, best first; duplicates last."""
    target = plan.target.column if plan.target else None
    base = []
    for fact in facts:
        size = fact.test.size if fact.test is not None else _DESCRIPTIVE_SIZE.get(fact.kind, 0.3)
        weights = [plan.relevance.get(c, 0.5) for c in fact.columns]
        relevance = sum(weights) / len(weights) if weights else 0.5
        if target and target in fact.columns:
            relevance = max(relevance, 0.8)
        check = rechecks.get(fact.id)
        stability = STABILITY[check.outcome] if check else 1.0
        raw = (
            WEIGHTS["evidence"] * evidence(fact)
            + WEIGHTS["size"] * min(1.0, size)
            + WEIGHTS["relevance"] * min(1.0, relevance)
        )
        base.append((fact, raw, evidence(fact), size, relevance, stability))
    base.sort(key=lambda t: (-t[1] * t[5], int(t[0].id[1:])))
    seen_columns: dict[frozenset[str], int] = {}
    seen_tests: dict[str, str] = {}
    seen_statements: dict[tuple[str, frozenset[str], tuple[str, ...]], str] = {}
    out: list[Ranked] = []
    for fact, raw, ev, size, relevance, stability in base:
        duplicate = None
        if fact.test is not None and fact.test.hypothesis in seen_tests:
            duplicate = seen_tests[fact.test.hypothesis]
        signature = (fact.kind, frozenset(fact.columns), fact.numbers)
        if duplicate is None and signature in seen_statements:
            duplicate = seen_statements[signature]
        columns = frozenset(fact.columns)
        count = seen_columns.get(columns, 0)
        novelty = NOVELTY[min(count, len(NOVELTY) - 1)]
        if duplicate is None:
            seen_columns[columns] = count + 1
            if fact.test is not None:
                seen_tests[fact.test.hypothesis] = fact.id
            seen_statements[signature] = fact.id
        out.append(
            Ranked(
                fact_id=fact.id,
                score=round(raw * novelty * stability, 6),
                evidence=round(ev, 6),
                size=round(size, 6),
                relevance=round(relevance, 6),
                novelty=novelty,
                stability=stability,
                duplicate_of=duplicate,
            )
        )
    out.sort(key=lambda r: (r.duplicate_of is not None, -r.score, int(r.fact_id[1:])))
    return out


def highlights(
    ranked: Sequence[Ranked], facts: Mapping[str, Fact], limit: int = HIGHLIGHTS
) -> list[str]:
    """The best findings for the summary: reliable ones first, then descriptive ones; never an
    inconclusive one, a duplicate, or a note about the data itself."""
    usable = [
        r
        for r in ranked
        if r.duplicate_of is None
        and facts[r.fact_id].tier != "inconclusive"
        and facts[r.fact_id].kind not in QUALITY_KINDS
    ]
    reliable = [r.fact_id for r in usable if facts[r.fact_id].tier in {"strong", "supported"}]
    described = [r.fact_id for r in usable if facts[r.fact_id].tier == "descriptive"]
    chosen: list[str] = []
    per_kind: dict[str, int] = {}
    for fact_id in reliable + described:
        kind = facts[fact_id].kind
        if per_kind.get(kind, 0) < PER_KIND:
            per_kind[kind] = per_kind.get(kind, 0) + 1
            chosen.append(fact_id)
    return chosen[:limit]
