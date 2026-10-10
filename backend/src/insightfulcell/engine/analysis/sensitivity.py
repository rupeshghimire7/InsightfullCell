"""The sensitivity check: do the top findings depend on the cleaning a person approved?

The ten strongest reliable findings are re-tested on minimally cleaned data: the same file with
only the lossless operations (trimming, standard missing values, parsing), none of the lossy
ones (merged categories, dropped duplicates, values set to empty, winsorising, filling in).
A finding then:

- **holds**: same direction, and still below the run's significance threshold;
- **weakens**: same direction, but no longer below the threshold;
- **flips**: the direction changes (a correlation's sign, or which group is highest or lowest);
- **not_retested**: the question could not be answered on the minimally cleaned data.

When no lossy operation was applied the two tables are the same, so nothing is re-run. Facts
are immutable: the outcome is recorded beside them (in the test log), not in them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.miners import Mined

TOP: Final = 10
_SIGNED: Final = frozenset(
    {"spearman", "pooled_spearman", "kendall_tau", "mean_difference", "share_difference"}
)
_RANKED_GROUPS: Final = frozenset({"group_comparison", "rate_comparison", "benchmark"})

Outcome = Literal["holds", "weakens", "flips", "not_retested", "same_data"]

NOTES: Final[dict[Outcome, str]] = {
    "holds": "It holds on minimally cleaned data.",
    "weakens": "On minimally cleaned data it is no longer reliable.",
    "flips": "On minimally cleaned data its direction changes.",
    "not_retested": "It could not be re-tested on minimally cleaned data.",
    "same_data": "No approved change removed or merged data, so there was nothing to re-test.",
}


@dataclass(frozen=True, slots=True)
class Recheck:
    fact_id: str
    hypothesis: str
    outcome: Outcome
    effect: float
    p_value: float
    retest_effect: float | None = None
    retest_p_value: float | None = None
    retest_n: int | None = None

    @property
    def note(self) -> str:
        return NOTES[self.outcome]


def top_findings(facts: Sequence[Fact], limit: int = TOP) -> list[Fact]:
    """The reliable findings to re-test: smallest q-value first, then the largest effect."""
    reliable = [f for f in facts if f.test is not None and f.tier in {"strong", "supported"}]
    return sorted(
        reliable,
        key=lambda f: (f.test.q_value, -f.test.size, int(f.id[1:])),  # type: ignore[union-attr]
    )[:limit]


def unchanged(chosen: Sequence[Fact]) -> list[Recheck]:
    """Every chosen finding, when the minimally cleaned data is the cleaned data."""
    return [_recheck(f, "same_data") for f in chosen]


def compare(chosen: Sequence[Fact], retest: Mined, threshold: float) -> list[Recheck]:
    """Each chosen finding against its own hypothesis re-tested on the minimal data."""
    by_hypothesis = {logged.test.hypothesis: logged for logged in retest.tests}
    facts: Mapping[str, Fact] = {f.id: f for x in retest.findings for f in x.facts}
    out: list[Recheck] = []
    for fact in chosen:
        assert fact.test is not None  # noqa: S101  (chosen by ``top_findings``)
        logged = by_hypothesis.get(fact.test.hypothesis)
        if logged is None:
            out.append(_recheck(fact, "not_retested"))
            continue
        again = logged.test
        other = facts.get(logged.fact_id) if logged.fact_id else None
        if _flipped(fact, again.effect, other):
            outcome: Outcome = "flips"
        elif again.p_value > threshold:
            outcome = "weakens"
        else:
            outcome = "holds"
        out.append(
            Recheck(
                fact_id=fact.id,
                hypothesis=fact.test.hypothesis,
                outcome=outcome,
                effect=fact.test.effect,
                p_value=fact.test.p_value,
                retest_effect=again.effect,
                retest_p_value=again.p_value,
                retest_n=again.n,
            )
        )
    return out


def _flipped(fact: Fact, effect: float, other: Fact | None) -> bool:
    assert fact.test is not None  # noqa: S101
    if fact.test.effect_name in _SIGNED and fact.test.effect * effect < 0:
        return True
    # Which group is highest and lowest is part of what a comparison says.
    return (
        fact.kind in _RANKED_GROUPS
        and other is not None
        and other.kind == fact.kind
        and other.labels[:2] != fact.labels[:2]
    )


def _recheck(fact: Fact, outcome: Outcome) -> Recheck:
    assert fact.test is not None  # noqa: S101
    return Recheck(
        fact_id=fact.id,
        hypothesis=fact.test.hypothesis,
        outcome=outcome,
        effect=fact.test.effect,
        p_value=fact.test.p_value,
    )
