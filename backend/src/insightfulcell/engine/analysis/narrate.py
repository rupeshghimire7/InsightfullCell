"""Narration: the answers written in words, every sentence checked against the facts it cites.

The model (prompt ``narrate-v1``) sees the plan's questions and the computed facts (statements
of numbers about the cleaned data, never rows) and writes a short answer per question plus a
summary, citing fact ids. The verifier then keeps a sentence only if:

- it cites at least one fact, and only facts of its own question (the summary may cite any);
- every number in it appears in a cited fact, as computed;
- it makes no causal or significance claim ("causes", "because", "due to", "significant", ...);
- it holds no link or email address (labels from the data can carry text that looks like an
  instruction; a sentence can repeat a label, never send the reader somewhere);
- a sentence resting only on inconclusive facts says so ("no reliable", "not", "unclear"): an
  unreliable difference is never stated as a finding;
- a summary sentence cites only the run's highlights candidates (reliable or descriptive
  findings), so the summary never leads with something the analysis could not support.

A question whose answer loses more than a third of its sentences, or all of them, is written
by the engine instead, from the facts' own statements. If the model is off or fails, the whole
report is written by the engine. Either way every sentence carries its citations.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.prompt import (
    PROMPT_ID,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    SectionInput,
    user_prompt,
)
from insightfulcell.engine.ports import (
    LanguageModel,
    ModelRequest,
    ModelUnavailable,
    Purpose,
)

logger = logging.getLogger(__name__)

MAX_SENTENCE_CHARS: Final = 500
MAX_DROPPED_SHARE: Final = 1 / 3
ENGINE_SENTENCES_PER_SECTION: Final = 3
SUMMARY_SECTIONS: Final = 4

_NUMBER: Final = re.compile(r"(?<![\w.])[-+]?[$€£]?\d[\d,]*(?:\.\d+)?%?")
_BANNED: Final = re.compile(
    r"\b(caus\w*|because|due to|leads? to|led to|result(?:s|ed)? in|proves?|proven|proof|"
    r"significan\w*|guarantee\w*|definitely|certainly)\b",
    re.IGNORECASE,
)


_LINK: Final = re.compile(r"https?://|\bwww\.|\b[\w.+-]+@[\w-]+\.\w", re.IGNORECASE)
_NEGATED: Final = re.compile(
    r"\b(no|not|neither|nor|unclear|inconclusive|unreliable|without)\b|n't\b", re.IGNORECASE
)
_UP: Final = re.compile(
    r"\b(rose|rise[sn]?|rising|increas\w*|grew|grow\w*|went up|climb\w*)\b", re.I
)
_DOWN: Final = re.compile(
    r"\b(fell|falls?|falling|decreas\w*|declin\w*|dropp?\w*|went down|shr[ae]nk\w*)\b", re.I
)


class Sentence(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    citations: tuple[str, ...]


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SentenceProposal(_Proposal):
    text: str = Field(description="One plain sentence. Numbers only as written in the facts.")
    citations: list[str] = Field(description="Ids of the facts this sentence rests on (F1, F2).")


class SectionProposal(_Proposal):
    analysis_id: str = Field(description="The question's id, exactly as given.")
    sentences: list[SentenceProposal]


class NarrationProposal(_Proposal):
    summary: list[SentenceProposal] = Field(
        description="Three to five sentences on the most important findings, each cited."
    )
    sections: list[SectionProposal]


@dataclass(frozen=True, slots=True)
class Narration:
    summary: tuple[Sentence, ...]
    sections: dict[str, tuple[Sentence, ...]]
    written_by: dict[str, Literal["model", "engine"]]
    source: Literal["model", "engine", "mixed"]
    provider: str | None = None
    model: str | None = None
    fallback_reason: str | None = None
    kept: int = 0
    dropped: int = 0


async def narrate(  # noqa: PLR0913  (keyword-only)
    model: LanguageModel,
    *,
    org_id: UUID,
    run_id: UUID,
    sections: Sequence[SectionInput],
    context: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    sensitive_values: frozenset[str],
    summary_facts: Sequence[str] | None = None,
    checks: Mapping[str, str] | None = None,
) -> Narration:
    """The report's words: the model's, verified sentence by sentence, else the engine's.
    ``summary_facts`` are the facts the summary may cite, best first (by default the first
    fact of each question)."""
    answered = [s for s in sections if s.facts]
    if not answered:
        return engine_narration(sections, reason="no_facts", summary_facts=summary_facts)
    if model.identity is None:
        return engine_narration(sections, reason="disabled", summary_facts=summary_facts)
    try:
        reply = await model.complete(
            ModelRequest(
                purpose=Purpose.NARRATE,
                org_id=org_id,
                run_id=run_id,
                prompt_id=PROMPT_ID,
                prompt_version=PROMPT_VERSION,
                system=SYSTEM_PROMPT,
                user=user_prompt(
                    answered, context, summary_facts=summary_facts or (), checks=checks
                ),
                response_model=NarrationProposal,
                response_schema=response_schema,
                sensitive_values=sensitive_values,
            )
        )
    except ModelUnavailable as exc:
        logger.info("narration fell back to the engine", extra={"reason": exc.reason})
        return engine_narration(sections, reason=exc.reason, summary_facts=summary_facts)
    assert isinstance(reply.data, NarrationProposal)  # noqa: S101  (the request's model)
    return verify(
        reply.data,
        sections,
        provider=reply.provider,
        model=reply.model,
        summary_facts=summary_facts,
    )


def verify(
    proposal: NarrationProposal,
    sections: Sequence[SectionInput],
    *,
    provider: str | None = None,
    model: str | None = None,
    summary_facts: Sequence[str] | None = None,
) -> Narration:
    by_id = {s.analysis_id: s for s in sections}
    every = {f.id: f for s in sections for f in s.facts}
    facts = (
        {k: every[k] for k in summary_facts if k in every} if summary_facts is not None else every
    )
    proposed = {p.analysis_id: p for p in proposal.sections if p.analysis_id in by_id}
    texts: dict[str, tuple[Sentence, ...]] = {}
    written: dict[str, Literal["model", "engine"]] = {}
    kept = dropped = 0
    for section in sections:
        if not section.facts:
            continue
        own = {f.id: f for f in section.facts}
        candidates = (
            proposed[section.analysis_id].sentences if section.analysis_id in proposed else []
        )
        good = [s for s in (_checked(c, own) for c in candidates) if s is not None]
        lost = len(candidates) - len(good)
        if good and lost / len(candidates) <= MAX_DROPPED_SHARE:
            texts[section.analysis_id] = tuple(good)
            written[section.analysis_id] = "model"
            kept += len(good)
            dropped += lost
        else:
            texts[section.analysis_id] = _engine_section(section)
            written[section.analysis_id] = "engine"
            dropped += len(candidates)
    summary = tuple(s for s in (_checked(c, facts) for c in proposal.summary) if s is not None)
    kept += len(summary)
    dropped += len(proposal.summary) - len(summary)
    if not summary:
        summary = _engine_summary(sections, summary_facts)
    by_model = sum(1 for w in written.values() if w == "model")
    source: Literal["model", "engine", "mixed"] = (
        "model" if by_model == len(written) else ("engine" if by_model == 0 else "mixed")
    )
    return Narration(
        summary=summary,
        sections=texts,
        written_by=written,
        source=source,
        provider=provider,
        model=model,
        kept=kept,
        dropped=dropped,
    )


def engine_narration(
    sections: Sequence[SectionInput],
    *,
    reason: str,
    summary_facts: Sequence[str] | None = None,
) -> Narration:
    """Every answer from the facts' own statements: plainer, and exact by construction."""
    texts = {s.analysis_id: _engine_section(s) for s in sections if s.facts}
    return Narration(
        summary=_engine_summary(sections, summary_facts),
        sections=texts,
        written_by=dict.fromkeys(texts, "engine"),
        source="engine",
        fallback_reason=reason,
    )


def _engine_section(section: SectionInput) -> tuple[Sentence, ...]:
    return tuple(
        Sentence(text=f.statement, citations=(f.id,))
        for f in section.facts[:ENGINE_SENTENCES_PER_SECTION]
    )


def _engine_summary(
    sections: Sequence[SectionInput], summary_facts: Sequence[str] | None = None
) -> tuple[Sentence, ...]:
    if summary_facts is not None:
        every = {f.id: f for s in sections for f in s.facts}
        leads = [every[k] for k in summary_facts if k in every][:SUMMARY_SECTIONS]
    else:
        leads = [s.facts[0] for s in sections if s.facts][:SUMMARY_SECTIONS]
    return tuple(Sentence(text=f.statement, citations=(f.id,)) for f in leads)


# --------------------------------------------------------------------------- checks


def _checked(proposal: SentenceProposal, allowed: Mapping[str, Fact]) -> Sentence | None:
    text = " ".join(proposal.text.split())
    citations = tuple(dict.fromkeys(c.strip() for c in proposal.citations if c.strip()))
    if not text or len(text) > MAX_SENTENCE_CHARS or not citations:
        return None
    if any(c not in allowed for c in citations):
        return None
    if _BANNED.search(text) or _LINK.search(text):
        return None
    cited = [allowed[c] for c in citations]
    if not numbers_supported(text, cited) or not direction_supported(text, cited):
        return None
    if not strength_supported(text, cited):
        return None
    return Sentence(text=text, citations=citations)


def strength_supported(text: str, cited: Sequence[Fact]) -> bool:
    """A sentence that rests only on inconclusive facts must say the result is not reliable:
    it may not present a difference the analysis could not support."""
    if cited and all(f.tier == "inconclusive" for f in cited):
        return bool(_NEGATED.search(text))
    return True


def numbers_supported(text: str, cited: Sequence[Fact]) -> bool:
    """Every number in ``text`` appears, at the precision written, in a cited fact."""
    pool = [_parse(m.group(0)) for f in cited for m in _NUMBER.finditer(_fact_text(f))]
    known = [p for p in pool if p is not None]
    for match in _NUMBER.finditer(text):
        token = _parse(match.group(0))
        if token is None:
            continue
        value, percent, decimals = token
        tolerance = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(
            percent == p and abs(value - v) <= max(tolerance, 0.5 * 10 ** (-d) + 1e-9)
            for v, p, d in known
        ):
            return False
    return True


def direction_supported(text: str, cited: Sequence[Fact]) -> bool:
    """A sentence that says a trend went up (or down) must cite trends that did."""
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    if up == down:
        return True
    for fact in cited:
        if fact.kind != "trend":
            continue
        first, last = fact.values.get("first"), fact.values.get("last")
        if not isinstance(first, int | float) or not isinstance(last, int | float):
            continue
        if (up and last < first) or (down and last > first):
            return False
    return True


def _fact_text(fact: Fact) -> str:
    return " ".join([fact.statement, *fact.numbers, *fact.labels])


def _parse(token: str) -> tuple[float, bool, int] | None:
    percent = token.endswith("%")
    raw = token.rstrip("%").replace(",", "").lstrip("+")
    raw = raw.replace("$", "").replace("€", "").replace("£", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    # Signs are compared in words ("fell by 24%"), by ``direction_supported``.
    return abs(value), percent, decimals
