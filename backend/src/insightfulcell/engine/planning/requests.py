"""A person's requests in their own words, understood without a model.

On the review page a person can write what else they want analysed ("compare labour hours
across service types") or what they do not need ("skip the postcode questions"). When a
language model is configured it reads the requests together with the plan; this module is the
engine's own reading, used when there is no model or the model fails, so a request is never
silently dropped.

The reading is deliberately simple and says what it understood: it finds the table's column
names in the text (raw or display names), looks for a few intent words (remove, compare, over
time, unusual, missing, ...), and turns that into a catalogue analysis checked by the same
validator as any planner's. Anything it cannot map is reported back as not understood, with a
reason, instead of guessed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from insightfulcell.engine.planning.language import (
    AnalysisProposal,
    MinerKind,
    RequestOutcome,
    RequestStatus,
    ResolvedAnalysis,
    Source,
)
from insightfulcell.engine.planning.validator import check_analysis
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.semantics import ColumnSemantics
from insightfulcell.engine.types import CATEGORICAL_MEANINGS, NUMERIC_MEANINGS, Meaning

MAX_REQUESTS: Final = 5
MAX_REQUEST_CHARS: Final = 500
MAX_COLUMNS_PER_REQUEST: Final = 3

_REMOVE: Final = re.compile(
    r"\b(remove|drop|skip|exclude|without|dont|don t|do not|no need|not interested|ignore|"
    r"stop|delete|not needed|unnecessary|leave out)\b"
)
_KIND_WORDS: Final[tuple[tuple[MinerKind, re.Pattern[str]], ...]] = (
    (MinerKind.DRIVER, re.compile(r"\b(drives?|drivers?|predicts?|influences?|affects?|"
                                  r"explains?|factors?|causes?|most associated)\b")),
    (MinerKind.MISSING_AS_SIGNAL, re.compile(r"\b(missing|empty|blank|gaps?)\b")),
    (MinerKind.ANOMALY, re.compile(r"\b(outliers?|unusual|anomal\w*|extreme|odd)\b")),
    (MinerKind.TREND, re.compile(r"\b(over time|trends?|monthly|weekly|yearly|by month|"
                                 r"by year|per month|seasonal\w*|growth|changes? over)\b")),
    (MinerKind.BENCHMARK, re.compile(r"\b(benchmark|above average|below average|"
                                     r"against the average|vs average|versus average)\b")),
    (MinerKind.CONDITIONAL, re.compile(r"\b(within each|within|for each|holds?|controlling)\b")),
    (MinerKind.DISTRIBUTION, re.compile(r"\b(distribution|distributed|spread|range|typical|"
                                        r"histogram)\b")),
    (MinerKind.RELATIONSHIP, re.compile(r"\b(relationship|related|relation|correlat\w*|"
                                        r"linked?|together)\b")),
    (MinerKind.SEGMENT, re.compile(r"\b(compare|comparison|across|differ\w*|breakdown|"
                                   r"split|per|by|versus|vs)\b")),
)  # fmt: skip


_QUESTIONS: Final[dict[MinerKind, str]] = {
    MinerKind.DISTRIBUTION: "How is {0} distributed?",
    MinerKind.RELATIONSHIP: "Is {0} related to {1}?",
    MinerKind.TREND: "How does {1} change over {0}?",
    MinerKind.SEGMENT: "Does {1} differ by {0}?",
    MinerKind.BENCHMARK: "Which {0} groups are above or below the average {1}?",
    MinerKind.ANOMALY: "Are there unusual values in {all}?",
    MinerKind.DRIVER: "What is most associated with {0}?",
    MinerKind.MISSING_AS_SIGNAL: "Does it matter when {0} is missing?",
    MinerKind.CONDITIONAL: "Does the link between {0} and {1} hold within each {2}?",
}


def question_for(kind: MinerKind, columns: Sequence[str], names: Mapping[str, str]) -> str:
    """The plain question a catalogue analysis answers, with the columns' display names."""
    shown = [names.get(c, c) for c in columns]
    return _QUESTIONS[kind].format(*shown, all=_and(shown))


@dataclass(frozen=True, slots=True)
class Intent:
    """What one request asks for, before it is applied to a plan."""

    request: str
    add: AnalysisProposal | None = None
    remove_columns: frozenset[str] = frozenset()
    remove_kinds: frozenset[MinerKind] = frozenset()
    #: Set when the request cannot be applied at all; ``note`` says why.
    not_understood: bool = False
    note: str = ""
    removing: bool = field(default=False)


def clean_requests(requests: Iterable[str]) -> tuple[str, ...]:
    """At most five non-empty requests, single-spaced and length-limited, duplicates dropped."""
    out: list[str] = []
    for raw in requests:
        text = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())
        text = text[:MAX_REQUEST_CHARS]
        if text and text.casefold() not in {o.casefold() for o in out}:
            out.append(text)
    return tuple(out[:MAX_REQUESTS])


def interpret(  # noqa: PLR0913  (keyword-only)
    requests: Sequence[str],
    *,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    names: Mapping[str, str],
    target: str | None,
    dataset_name: str,
) -> list[Intent]:
    return [
        _one(text, semantics=semantics, profile=profile, names=names, target=target,
             dataset_name=dataset_name)
        for text in clean_requests(requests)
    ]  # fmt: skip


def apply(
    analyses: Sequence[ResolvedAnalysis], intents: Sequence[Intent], names: Mapping[str, str]
) -> tuple[list[ResolvedAnalysis], list[RequestOutcome]]:
    """The plan's analyses with the requests applied, and an outcome for every request."""
    current = list(analyses)
    outcomes: list[RequestOutcome] = []
    added: list[ResolvedAnalysis] = []
    for intent in intents:
        if intent.not_understood:
            outcomes.append(_outcome(intent.request, RequestStatus.NOT_UNDERSTOOD, intent.note))
            continue
        if intent.removing:
            keep = [a for a in current if not _matches(a, intent)]
            removed = len(current) - len(keep)
            current = keep
            if removed:
                what = "question" if removed == 1 else "questions"
                outcomes.append(
                    _outcome(intent.request, RequestStatus.REMOVED, f"Removed {removed} {what}.")
                )
            else:
                outcomes.append(
                    _outcome(
                        intent.request,
                        RequestStatus.NOT_UNDERSTOOD,
                        "No planned question matched what you asked to remove.",
                    )
                )
            continue
        assert intent.add is not None  # noqa: S101  (an understood, non-removing request)
        item = intent.add
        key = (item.kind, frozenset(item.columns))
        existing = next(
            (a for a in [*current, *added] if (a.kind, frozenset(a.columns)) == key), None
        )
        if existing is not None:
            outcomes.append(
                _outcome(
                    intent.request,
                    RequestStatus.ALREADY_PLANNED,
                    f"Already planned: {existing.question}",
                )
            )
            continue
        columns = tuple(item.columns)
        resolved = ResolvedAnalysis(
            id=f"{item.kind.value}:{','.join(columns)}",
            kind=item.kind,
            columns=columns,
            question=item.question,
            priority=1,
            source=Source.PERSON,
        )
        added.append(resolved)
        outcomes.append(_outcome(intent.request, RequestStatus.ADDED, f"Added: {item.question}"))
    del names
    return [*added, *current], outcomes


# --------------------------------------------------------------------------- reading


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold().replace("'", "")).split())


def _mentions(
    text: str, semantics: Sequence[ColumnSemantics], names: Mapping[str, str]
) -> list[str]:
    """Columns named in the text (raw or display name, singular or plural), in the order
    they appear. Longer names win, so "Labour Cost" is not also read as "Cost"."""
    norm = f" {_normalise(text)} "
    phrases: list[tuple[str, str]] = []
    for s in semantics:
        for candidate in {s.name, names.get(s.name, s.name)}:
            phrase = _normalise(candidate)
            if len(phrase) >= 2:
                phrases.append((phrase, s.name))
    phrases.sort(key=lambda p: len(p[0]), reverse=True)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for phrase, column in phrases:
        for match in re.finditer(rf"(?<= ){re.escape(phrase)}s?(?= )", norm):
            span = (match.start(), match.end())
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            if column not in {c for _, c in found}:
                found.append((span[0], column))
    return [column for _, column in sorted(found)]


def _one(  # noqa: PLR0911, PLR0913  (one outcome per case)
    text: str,
    *,
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    names: Mapping[str, str],
    target: str | None,
    dataset_name: str,
) -> Intent:
    norm = _normalise(text)
    by_name = {s.name: s for s in semantics}
    mentioned = _mentions(text, semantics, names)
    personal = [c for c in mentioned if by_name[c].personal_data is not None]
    if personal:
        return Intent(
            text,
            not_understood=True,
            note=f"{names.get(personal[0], personal[0])} holds personal data, which is never "
            "analysed.",
        )
    kinds = [kind for kind, pattern in _KIND_WORDS if pattern.search(norm)]

    if _REMOVE.search(norm):
        remove_kinds = frozenset(k for k in kinds if k is not MinerKind.SEGMENT)
        if not mentioned and not remove_kinds:
            return Intent(
                text,
                not_understood=True,
                note="Name a column or a kind of question to remove.",
            )
        return Intent(
            text, removing=True, remove_columns=frozenset(mentioned), remove_kinds=remove_kinds
        )

    if MinerKind.DRIVER in kinds and (not mentioned or mentioned == [target]):
        if target is None:
            return Intent(
                text,
                not_understood=True,
                note="Choose an outcome to explain first; then this question is answered.",
            )
        return _checked(text, MinerKind.DRIVER, [target], semantics, profile, names, target,
                        dataset_name)  # fmt: skip
    if not mentioned:
        return Intent(
            text,
            not_understood=True,
            note="No column name was found. Use the column names shown on this page.",
        )
    if len(mentioned) > MAX_COLUMNS_PER_REQUEST:
        return Intent(text, not_understood=True, note="Ask about up to three columns at a time.")
    if MinerKind.DRIVER in kinds:
        return Intent(
            text,
            not_understood=True,
            note=f"To see what is most associated with {names.get(mentioned[0], mentioned[0])}, "
            "choose it as the outcome to explain.",
        )
    kind, columns = _shape(mentioned, kinds, by_name, target)
    if kind is None:
        return Intent(
            text,
            not_understood=True,
            note="These columns do not fit any kind of question the analysis can answer.",
        )
    return _checked(text, kind, columns, semantics, profile, names, target, dataset_name)


def _shape(  # noqa: PLR0911  (a decision table)
    mentioned: list[str],
    kinds: list[MinerKind],
    by_name: Mapping[str, ColumnSemantics],
    target: str | None,
) -> tuple[MinerKind | None, list[str]]:
    meanings = {c: by_name[c].meaning for c in mentioned}
    numeric = [c for c in mentioned if meanings[c] in NUMERIC_MEANINGS]
    categorical = [c for c in mentioned if meanings[c] in CATEGORICAL_MEANINGS]
    dates = [c for c in mentioned if meanings[c] is Meaning.DATETIME]
    first_date = next((s.name for s in by_name.values() if s.meaning is Meaning.DATETIME), None)

    if MinerKind.MISSING_AS_SIGNAL in kinds:
        extra = [target] if target and target not in mentioned else []
        return MinerKind.MISSING_AS_SIGNAL, [mentioned[0], *extra][:2]
    if MinerKind.ANOMALY in kinds and numeric and len(numeric) == len(mentioned):
        return MinerKind.ANOMALY, numeric
    if len(mentioned) == 1:
        only = mentioned[0]
        if MinerKind.TREND in kinds and only in numeric and first_date:
            return MinerKind.TREND, [first_date, only]
        if meanings[only] is Meaning.TEXT:
            return None, []
        return MinerKind.DISTRIBUTION, [only]
    if len(mentioned) == 2:
        if dates and numeric:
            return MinerKind.TREND, [dates[0], numeric[0]]
        if categorical and numeric:
            kind = MinerKind.BENCHMARK if MinerKind.BENCHMARK in kinds else MinerKind.SEGMENT
            return kind, [categorical[0], numeric[0]]
        if len(categorical) == 2:
            if target in categorical:
                other = next(c for c in categorical if c != target)
                return MinerKind.SEGMENT, [other, target]
            return MinerKind.RELATIONSHIP, categorical
        if len(numeric) == 2:
            return MinerKind.RELATIONSHIP, numeric
        return None, []
    if categorical:
        group = categorical[-1]
        pair = [c for c in mentioned if c != group]
        return MinerKind.CONDITIONAL, [*pair, group]
    return None, []


def _checked(  # noqa: PLR0913, PLR0917
    text: str,
    kind: MinerKind,
    columns: list[str],
    semantics: Sequence[ColumnSemantics],
    profile: Profile,
    names: Mapping[str, str],
    target: str | None,
    dataset_name: str,
) -> Intent:
    proposal = AnalysisProposal(
        kind=kind, columns=columns, question=question_for(kind, columns, names), priority=1
    )
    accepted, rejected = check_analysis(
        proposal, semantics, profile, target=target, dataset_name=dataset_name
    )
    if accepted is None:
        reason = rejected[0].reason if rejected else "It does not fit the analysis catalogue."
        return Intent(text, not_understood=True, note=reason)
    return Intent(text, add=accepted)


def _matches(analysis: ResolvedAnalysis, intent: Intent) -> bool:
    if analysis.source is Source.PERSON and not intent.remove_columns and not intent.remove_kinds:
        return False
    columns_ok = not intent.remove_columns or bool(intent.remove_columns & set(analysis.columns))
    kinds_ok = not intent.remove_kinds or analysis.kind in intent.remove_kinds
    return columns_ok and kinds_ok


def _outcome(request: str, status: RequestStatus, note: str) -> RequestOutcome:
    return RequestOutcome(request=request, status=status, note=note, source="rules")


def _and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
