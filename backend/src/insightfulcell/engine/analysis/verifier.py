"""The sentence verifier: numbers through slots, then six checks on every sentence.

The narrator never writes a number. Each fact's statement is shown with its numbers replaced
by slots (``{F3.1}`` is the first number of fact F3) and the model writes the slots; the
renderer fills each slot with the fact's own formatted value, so a wrong number cannot be
written. A sentence is then accepted only if every check passes:

- ``citation``: it cites at least one fact, only facts given to its section, and uses slots of
  cited facts only.
- ``stray_number``: no digit outside a slot (labels with digits, such as "2025" or "Q1", may be
  repeated), and no numbers in words ("twice", "thousands", "percent").
- ``direction``: "rises" or "positive" only on facts that go up, and the reverse.
- ``strength``: strength words match the fact's effect-size band ("strong" needs a strong
  effect; "skyrocketed" is never right for a 2% move), and a sentence resting only on
  inconclusive facts that claims a difference, trend or link says it is not reliable
  (restating their values is fine).
- ``causal``: associations only; no "causes", "drives", "because", "leads to", "proves" or
  "significant".
- ``condition``: a number that holds for a group, a period or a place ("for North", "in 2025",
  "within each region") is stated with that condition.

Plus the form of the sentence (length, no leftover braces, no links or email addresses: labels
from the data can carry text that looks like an instruction).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from insightfulcell.engine.analysis import facts as fmt
from insightfulcell.engine.analysis.facts import Fact

MAX_SENTENCE_CHARS: Final = 500

CheckName = Literal[
    "form", "citation", "stray_number", "direction", "strength", "causal", "condition", "judge"
]
CHECKS: Final[tuple[CheckName, ...]] = (
    "form", "citation", "stray_number", "direction", "strength", "causal", "condition", "judge"
)  # fmt: skip

SLOT: Final = re.compile(r"\{(F\d{1,6})\.(\d{1,3})\}")
_BRACE: Final = re.compile(r"[{}]")
_DIGIT: Final = re.compile(r"\d")
_NUMBER_WORDS: Final = re.compile(
    r"\b(percent|per cent|percentage points?|twice|double[sd]?|doubling|triple[sd]?|tripling|"
    r"quadrupl\w*|halved|halving|\w+fold|dozens?|hundreds?|thousands?|millions?|billions?)\b",
    re.IGNORECASE,
)
_CAUSAL: Final = re.compile(
    r"\b(caus\w*|because|due to|owing to|thanks to|leads? to|led to|result(?:s|ed)? in|"
    r"driv(?:e|es|en|ing)|drove|drivers? of|impact\w*|influenc\w*|affects|effect of|"
    r"proves?|proven|proof|significan\w*|guarantee\w*|definitely|certainly)\b",
    re.IGNORECASE,
)
_LINK: Final = re.compile(r"https?://|\bwww\.|\b[\w.+-]+@[\w-]+\.\w", re.IGNORECASE)
NEGATED: Final = re.compile(
    r"\b(no|not|neither|nor|unclear|inconclusive|unreliable|without)\b|n't\b", re.IGNORECASE
)
_UP: Final = re.compile(
    r"\b(rose|rise[sn]?|rising|increas\w*|grew|grow\w*|went up|climb\w*|upward|positive\w*|"
    r"together)\b",
    re.IGNORECASE,
)
_DOWN: Final = re.compile(
    r"\b(fell|falls?|falling|decreas\w*|declin\w*|dropp?\w*|went down|shr[ae]nk\w*|downward|"
    r"negative\w*|opposite|inverse\w*)\b",
    re.IGNORECASE,
)
#: Words that claim a difference, a change or a link (as opposed to restating values).
_CLAIM: Final = re.compile(
    r"\b(higher|lower|more|less|greater|fewer|larger|smaller|differ\w*|difference\w*|vary|"
    r"varies|link\w*|associat\w*|relat\w*|correlat\w*|goes with|go with|tends?|trend\w*|"
    r"strong\w*|moderate\w*)\b",
    re.IGNORECASE,
)
_CONDITION_WORDS: Final = ("within",)
_CLAUSE_END: Final = re.compile(r"[;()]|,\s|:\s|\.\s|\.$|\s(?:and|but|while|whereas)\s")
_EVIDENCE_PHRASE: Final = re.compile(r"\bstrong(?:ly)?\s+(?:evidence|support\w*)\b", re.I)

Band = Literal["negligible", "weak", "moderate", "strong"]
#: Wording lexicon: each word is allowed only on facts whose effect size is in its bands.
LEXICON: Final[tuple[tuple[re.Pattern[str], frozenset[Band], bool], ...]] = (
    # Hyperbole: only for a strong effect, and never on a sentence without a tested fact.
    (
        re.compile(
            r"\b(skyrocket\w*|soar\w*|plummet\w*|plung\w*|explo(?:de|ded|sive|sion)\w*|"
            r"dramatic\w*|drastic\w*|massive\w*|huge|enormous\w*|staggering\w*|remarkabl\w*|"
            r"extraordinar\w*|surg(?:e|ed|es|ing)|spectacular\w*|tremendous\w*)\b",
            re.I,
        ),
        frozenset({"strong"}),
        True,
    ),
    (
        re.compile(
            r"\b(strong(?:ly|er|est)?|sharp(?:ly)?|substantial(?:ly)?|marked(?:ly)?|steep(?:ly)?|"
            r"considerabl[ey]|pronounced|powerful(?:ly)?)\b",
            re.I,
        ),
        frozenset({"strong"}),
        False,
    ),
    (re.compile(r"\b(moderate(?:ly)?)\b", re.I), frozenset({"moderate"}), False),
    (
        re.compile(r"\b(weak(?:ly|er|est)?|slight(?:ly)?|modest(?:ly)?|mild(?:ly)?)\b", re.I),
        frozenset({"weak", "negligible"}),
        False,
    ),
    (
        re.compile(r"\b(negligibl[ey]|barely|hardly|tiny|little to no)\b", re.I),
        frozenset({"negligible"}),
        False,
    ),
)


@dataclass(frozen=True, slots=True)
class Slot:
    value: str  # the fact's formatted number
    conditions: tuple[str, ...]  # one of these must be named when the slot is used


@dataclass(frozen=True, slots=True)
class SlottedFact:
    fact: Fact
    statement: str  # the statement with its numbers as slots
    slots: dict[str, Slot]  # "F3.1" -> the number and its conditions
    literals: tuple[str, ...]  # text with digits a sentence may repeat (labels, names)


@dataclass(frozen=True, slots=True)
class Verdict:
    text: str  # filled in when accepted, as written when rejected
    citations: tuple[str, ...]
    check: CheckName | None = None
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.check is None


def slotted(fact: Fact) -> SlottedFact:
    """The fact's statement with each of its numbers replaced by a slot, in order."""
    statement, values, position = fact.statement, {}, 0
    for i, number in enumerate(fact.numbers, start=1):
        key, token = f"{fact.id}.{i}", "{" + f"{fact.id}.{i}" + "}"
        values[key] = number
        # A number never matches inside a slot already placed: its digits follow "F" or ".".
        pattern = re.compile(r"(?<![\w.])" + re.escape(number) + r"(?![\w%]|\.\d)")
        match = pattern.search(statement, position) or pattern.search(statement)
        if match is not None:
            statement = statement[: match.start()] + token + statement[match.end() :]
            position = match.start() + len(token)
    placed = {
        f"{m.group(1)}.{m.group(2)}": _conditions(statement, m.start(), m.end(), fact.labels)
        for m in SLOT.finditer(statement)
    }
    slots = {k: Slot(v, placed.get(k, ())) for k, v in values.items()}
    remainder = SLOT.sub(" ", statement)
    literals = {label for label in fact.labels if _DIGIT.search(label)}
    literals.update(w for w in re.findall(r"[^\s,;:()]+", remainder) if _DIGIT.search(w))
    return SlottedFact(
        fact=fact,
        statement=statement,
        slots=slots,
        literals=tuple(sorted({w.rstrip(".") for w in literals}, key=len, reverse=True)),
    )


def _conditions(statement: str, start: int, end: int, labels: Sequence[str]) -> tuple[str, ...]:
    """The labels named in the slot's clause (and "within" for a within-groups number)."""
    before = [m.end() for m in _CLAUSE_END.finditer(statement, 0, start)]
    after = _CLAUSE_END.search(statement, end)
    clause = statement[before[-1] if before else 0 : after.start() if after else len(statement)]
    found = [label for label in labels if label and _names(clause, label)]
    found += [w for w in _CONDITION_WORDS if _names(clause, w)]
    return tuple(dict.fromkeys(found))


def _names(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE) is not None


def render(text: str, slots: Mapping[str, Slot]) -> str:
    return SLOT.sub(lambda m: slots[f"{m.group(1)}.{m.group(2)}"].value, text)


def band(fact: Fact) -> frozenset[Band]:
    """The effect-size bands a fact's wording may use: its size's band, and any band word its
    own statement uses. A fact without a test has none."""
    if fact.test is None:
        return frozenset()
    bands: set[Band] = {_as_band(fmt.strength(fact.test.size))}
    for word in ("negligible", "weak", "moderate", "strong"):
        if re.search(rf"\b{word}\b", fact.statement):
            bands.add(_as_band(word))
    return frozenset(bands)


def _as_band(word: str) -> Band:
    match word:
        case "negligible" | "weak" | "moderate" | "strong":
            return word
    return "negligible"


def check(  # noqa: PLR0911  (one check after another, first failure wins)
    text: str, citations: Sequence[str], allowed: Mapping[str, SlottedFact]
) -> Verdict:
    """Verify one proposed sentence against the facts its section was given."""
    text = " ".join(text.split())
    cited = tuple(dict.fromkeys(c.strip() for c in citations if c.strip()))
    if not text or len(text) > MAX_SENTENCE_CHARS:
        return Verdict(text, cited, "form", "The sentence is empty or too long.")
    if not cited:
        return Verdict(text, cited, "citation", "The sentence cites no fact.")
    stranger = [c for c in cited if c not in allowed]
    if stranger:
        return Verdict(
            text, cited, "citation", f"It cites {', '.join(stranger)}, not given for this part."
        )
    slots = {k: s for c in cited for k, s in allowed[c].slots.items()}
    used = [f"{m.group(1)}.{m.group(2)}" for m in SLOT.finditer(text)]
    unknown = [u for u in used if u not in slots]
    if unknown:
        return Verdict(text, cited, "citation", f"Slot {{{unknown[0]}}} belongs to no cited fact.")
    bare = SLOT.sub(" ", text)
    if _BRACE.search(bare):
        return Verdict(text, cited, "form", "Braces outside a slot.")
    if _LINK.search(bare):
        return Verdict(text, cited, "form", "Links and email addresses are not allowed.")
    if _stray(bare, [allowed[c] for c in cited]):
        return Verdict(text, cited, "stray_number", "A number is written outside a slot.")
    facts = [allowed[c].fact for c in cited]
    if (found := _CAUSAL.search(bare)) is not None:
        return Verdict(text, cited, "causal", f'"{found.group(0)}" claims more than association.')
    if not direction_supported(bare, facts):
        return Verdict(text, cited, "direction", "The direction is the opposite of the facts'.")
    if (problem := strength_problem(bare, facts)) is not None:
        return Verdict(text, cited, "strength", problem)
    for key in dict.fromkeys(used):
        conditions = slots[key].conditions
        if conditions and not any(_names(bare, c) for c in conditions):
            return Verdict(
                text,
                cited,
                "condition",
                f"{{{key}}} holds only for {' or '.join(conditions[:3])}; say so.",
            )
    return Verdict(render(text, slots), cited)


def _stray(bare: str, cited: Sequence[SlottedFact]) -> bool:
    if _NUMBER_WORDS.search(bare):
        return True
    rest = bare
    for literal in sorted({w for s in cited for w in s.literals}, key=len, reverse=True):
        rest = re.sub(r"(?<!\w)" + re.escape(literal) + r"(?!\w)", " ", rest, flags=re.I)
    return _DIGIT.search(rest) is not None


def direction_supported(text: str, cited: Sequence[Fact]) -> bool:
    """A sentence that says something went up (or a link is positive) must cite facts that
    did; and the reverse. Negated sentences ("no reliable upward trend") are not judged."""
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    if up == down or NEGATED.search(text):
        return True
    signs = [s for s in (_sign(f) for f in cited) if s is not None]
    return not any((up and s < 0) or (down and s > 0) for s in signs)


def _sign(fact: Fact) -> float | None:
    values = fact.values
    if fact.kind == "trend":
        first, last = values.get("first"), values.get("last")
        if not isinstance(first, int | float) or not isinstance(last, int | float):
            return None
        change = float(last) - float(first)
        # The ends and the overall trend can disagree (a dip at the end of a rise): then
        # either direction describes part of the fact, and neither is judged.
        if fact.test is not None and (fact.test.effect > 0) != (change > 0):
            return None
        return change
    if fact.kind == "correlation":
        rho = values.get("spearman")
        return float(rho) if isinstance(rho, int | float) else None
    if fact.kind == "driver" and fact.test is not None and fact.test.effect_name != "eta":
        return fact.test.effect
    return None


def strength_problem(text: str, cited: Sequence[Fact]) -> str | None:
    """Why the sentence's wording is stronger (or weaker) than its facts, or ``None``."""
    # An inconclusive fact's values are exact; its difference, trend or link is not. Restating
    # the values is fine, claiming the difference is not (unless the sentence says so).
    if (
        cited
        and all(f.tier == "inconclusive" for f in cited)
        and not NEGATED.search(text)
        and (_CLAIM.search(text) or _UP.search(text) or _DOWN.search(text))
    ):
        return "The facts are not reliable; say there is no reliable difference or link."
    if any(f.tier == "strong" for f in cited):
        text = _EVIDENCE_PHRASE.sub(" ", text)
    bands = frozenset().union(*(band(f) for f in cited)) if cited else frozenset()
    for pattern, allowed, hyperbole in LEXICON:
        found = pattern.search(text)
        if found is None:
            continue
        if not bands:
            if hyperbole:
                return f'"{found.group(0)}" overstates a descriptive fact.'
            continue
        if not bands & allowed:
            size = " or ".join(sorted(bands))
            return f'"{found.group(0)}" does not match a {size} effect.'
    return None
