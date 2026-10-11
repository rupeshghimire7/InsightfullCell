"""Narration: the answers written in words, every sentence verified against its facts.

One model call per part of the report, sections concurrently, the summary last:

1. **Section.** The model (prompt ``narrate-v3``) gets one question and that question's facts,
   with every number as a slot (``{F3.1}``), plus the run's context (what the table is, how it
   was prepared; never rows). It answers in sentences that write numbers only as slots and
   list the facts they rest on.
2. **Verify.** Each sentence goes through ``engine.analysis.verifier`` (citations, stray
   numbers, direction, strength words, causal language, conditions) and, when switched on,
   the judge, a second model that can only reject. Accepted sentences get their slots filled
   from the facts, so a wrong number cannot be written.
3. **Repair.** Failed sentences are asked for once more, with each failure as feedback. One
   that fails again is replaced by the template sentence: its fact's own statement.
4. **Fallback.** A part where more than 30% of the sentences still fail is written from
   templates altogether, and the prompt is flagged in the log. If the model is unavailable
   (an outage, an open breaker, a spent budget), the part is written from templates and the
   report is marked "narration simplified"; it can be narrated again later.
5. **Summary.** Last, from the ranked highlights that the verified answers rest on, and
   verified the same way.

Every verdict, accepted or not, is kept (``records``) for the run's verification results.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.prompt import (
    JUDGE_PROMPT_ID,
    JUDGE_PROMPT_VERSION,
    JUDGE_SYSTEM_PROMPT,
    PROMPT_ID,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    SectionInput,
    judge_prompt,
    repair_prompt,
    section_prompt,
    summary_prompt,
)
from insightfulcell.engine.analysis.verifier import SlottedFact, Verdict, check, slotted
from insightfulcell.engine.planning.schema import response_schema
from insightfulcell.engine.ports import (
    LanguageModel,
    ModelRequest,
    ModelUnavailable,
    Purpose,
    VerificationRecord,
)

logger = logging.getLogger(__name__)

SUMMARY: Final = "summary"
MAX_FAILED_SHARE: Final = 0.3
MAX_SECTION_SENTENCES: Final = 5
MAX_SUMMARY_SENTENCES: Final = 6
ENGINE_SENTENCES_PER_SECTION: Final = 3
SUMMARY_SECTIONS: Final = 4
SUMMARY_FACTS: Final = 8
DEFAULT_CONCURRENCY: Final = 4
#: Model failures that narrating again later cannot fix.
FINAL_REASONS: Final = frozenset({"disabled", "no_facts", "payload_blocked", "budget_run"})


class Sentence(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    citations: tuple[str, ...]
    #: Who wrote it: the model (verified) or the engine (a fact's own statement).
    written_by: Literal["model", "engine"] | None = None


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SentenceProposal(_Proposal):
    text: str = Field(description="One plain sentence. Numbers only as slots, like {F3.1}.")
    citations: list[str] = Field(description="Ids of the facts this sentence rests on (F1, F2).")


class DraftProposal(_Proposal):
    sentences: list[SentenceProposal]


class JudgeVerdict(_Proposal):
    accept: bool = Field(description="True if the facts support everything the sentence says.")
    reason: str = Field(description="If rejected, what the facts do not support, in a phrase.")


@dataclass(frozen=True, slots=True)
class Narration:
    summary: tuple[Sentence, ...]
    sections: dict[str, tuple[Sentence, ...]]
    written_by: dict[str, Literal["model", "engine"]]
    source: Literal["model", "engine", "mixed"]
    provider: str | None = None
    model: str | None = None
    fallback_reason: str | None = None
    kept: int = 0  # model sentences accepted (first draft or repair)
    dropped: int = 0  # model sentences that failed for good
    repaired: int = 0  # accepted on the second attempt
    #: Parts where more than 30% failed: written from templates, their prompt flagged.
    flagged: tuple[str, ...] = ()
    #: The model was unavailable for some part: written from templates instead.
    simplified: bool = False
    #: Narrating again later may succeed (the model was out, not off or refused).
    retryable: bool = False
    judge: Literal["off", "on"] = "off"
    records: tuple[VerificationRecord, ...] = ()
    prompt_version: str | None = None

    @property
    def rejected(self) -> dict[str, int]:
        """Rejections per check, first drafts and repairs."""
        return dict(Counter(r.check for r in self.records if r.check is not None))


async def narrate(  # noqa: PLR0913  (keyword-only)
    model: LanguageModel,
    *,
    org_id: UUID,
    run_id: UUID,
    sections: Sequence[SectionInput],
    context: Mapping[str, Any],
    sensitive_values: frozenset[str],
    summary_facts: Sequence[str] | None = None,
    checks: Mapping[str, str] | None = None,
    judge: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> Narration:
    """The report's words: the model's, verified sentence by sentence, else the engine's.
    ``summary_facts`` are the facts the summary may cite, best first (by default the first
    fact of each question); it cites only those the verified answers rest on."""
    answered = [s for s in sections if s.facts]
    if not answered:
        return engine_narration(sections, reason="no_facts", summary_facts=summary_facts)
    if model.identity is None:
        return engine_narration(sections, reason="disabled", summary_facts=summary_facts)
    writer = _Writer(
        model,
        org_id=org_id,
        run_id=run_id,
        context=context,
        sensitive_values=sensitive_values,
        checks=checks or {},
        judge=judge,
        concurrency=concurrency,
    )
    slots = {f.id: slotted(f) for s in answered for f in s.facts}
    parts = await asyncio.gather(
        *(
            writer.part(
                s.analysis_id,
                {f.id: slots[f.id] for f in s.facts},
                section_prompt(s.question, [slots[f.id] for f in s.facts], context, checks),
                templates=s.facts[:ENGINE_SENTENCES_PER_SECTION],
                limit=MAX_SECTION_SENTENCES,
            )
            for s in answered
        )
    )
    by_section = {s.analysis_id: p for s, p in zip(answered, parts, strict=True)}

    # The summary, last: only highlights the verified answers rest on.
    verified = {c for p in parts for sentence in p.sentences for c in sentence.citations}
    order = list(summary_facts) if summary_facts is not None else [s.facts[0].id for s in answered]
    allowed = [k for k in dict.fromkeys(order) if k in verified and k in slots][:SUMMARY_FACTS]
    if allowed:
        summary = await writer.part(
            SUMMARY,
            {k: slots[k] for k in allowed},
            summary_prompt([slots[k] for k in allowed], context, checks),
            templates=[slots[k].fact for k in allowed[:SUMMARY_SECTIONS]],
            limit=MAX_SUMMARY_SENTENCES,
        )
    else:
        summary = _Part(_engine_summary(sections, summary_facts), by_model=False)

    every = [*parts, summary]
    outages = [p.outage for p in every if p.outage is not None]
    flagged = tuple(name for name, p in [*by_section.items(), (SUMMARY, summary)] if p.flagged)
    if flagged:
        logger.warning(
            "narration prompt flagged: a part failed verification",
            extra={"prompt_version": PROMPT_VERSION, "parts": len(flagged)},
        )
    by_model = [p.by_model for p in every]
    source: Literal["model", "engine", "mixed"] = (
        "model" if all(by_model) else ("engine" if not any(by_model) else "mixed")
    )
    reason = outages[0] if outages else None
    return Narration(
        summary=summary.sentences,
        sections={k: p.sentences for k, p in by_section.items()},
        written_by={k: "model" if p.by_model else "engine" for k, p in by_section.items()},
        source=source,
        provider=writer.provider,
        model=writer.model_name,
        fallback_reason=reason,
        kept=sum(p.kept for p in every),
        dropped=sum(p.dropped for p in every),
        repaired=sum(p.repaired for p in every),
        flagged=flagged,
        simplified=bool(outages),
        retryable=reason is not None and reason not in FINAL_REASONS,
        judge="on" if judge else "off",
        records=tuple(writer.records),
        prompt_version=PROMPT_VERSION if writer.records else None,
    )


def engine_narration(
    sections: Sequence[SectionInput],
    *,
    reason: str,
    summary_facts: Sequence[str] | None = None,
) -> Narration:
    """Every answer from the facts' own statements: plainer, and exact by construction."""
    texts = {s.analysis_id: _template(s.facts[:ENGINE_SENTENCES_PER_SECTION])
             for s in sections if s.facts}  # fmt: skip
    simplified = reason not in {"disabled", "no_facts"}
    return Narration(
        summary=_engine_summary(sections, summary_facts),
        sections=texts,
        written_by=dict.fromkeys(texts, "engine"),
        source="engine",
        fallback_reason=reason,
        simplified=simplified,
        retryable=simplified and reason not in FINAL_REASONS,
    )


def _template(facts: Sequence[Fact]) -> tuple[Sentence, ...]:
    return tuple(Sentence(text=f.statement, citations=(f.id,), written_by="engine") for f in facts)


def _engine_summary(
    sections: Sequence[SectionInput], summary_facts: Sequence[str] | None = None
) -> tuple[Sentence, ...]:
    if summary_facts is not None:
        every = {f.id: f for s in sections for f in s.facts}
        leads = [every[k] for k in summary_facts if k in every][:SUMMARY_SECTIONS]
    else:
        leads = [s.facts[0] for s in sections if s.facts][:SUMMARY_SECTIONS]
    return _template(leads)


# --------------------------------------------------------------------------- one part


@dataclass(frozen=True, slots=True)
class _Part:
    sentences: tuple[Sentence, ...]
    by_model: bool
    outage: str | None = None  # why the model could not write this part
    kept: int = 0
    dropped: int = 0
    repaired: int = 0
    flagged: bool = False


@dataclass
class _Writer:
    model: LanguageModel
    org_id: UUID
    run_id: UUID
    context: Mapping[str, Any]
    sensitive_values: frozenset[str]
    checks: Mapping[str, str]
    judge: bool
    concurrency: int
    records: list[VerificationRecord] = field(default_factory=list)
    provider: str | None = None
    model_name: str | None = None

    def __post_init__(self) -> None:
        self._limit = asyncio.Semaphore(max(1, self.concurrency))

    async def part(
        self,
        name: str,
        allowed: Mapping[str, SlottedFact],
        user: str,
        *,
        templates: Sequence[Fact],
        limit: int,
    ) -> _Part:
        try:
            draft = await self._draft(user)
        except ModelUnavailable as exc:
            logger.info("narration part fell back to templates", extra={"reason": exc.reason})
            return _Part(_template(templates), by_model=False, outage=exc.reason)
        proposals = draft.sentences[:limit]
        if not proposals:
            return _Part(_template(templates), by_model=False, flagged=True)
        verdicts = [await self._verify(name, i, 1, p, allowed) for i, p in enumerate(proposals)]
        failed = [i for i, v in enumerate(verdicts) if not v.accepted]
        repaired, outage = 0, None
        if failed:
            try:
                fixes = (
                    await self._draft(
                        repair_prompt(
                            list(allowed.values()),
                            self.context,
                            [(verdicts[i].text, verdicts[i].detail or "") for i in failed],
                            self.checks,
                        )
                    )
                ).sentences
            except ModelUnavailable as exc:
                fixes, outage = [], exc.reason
            for i, fix in zip(failed, fixes, strict=False):
                verdict = await self._verify(name, i, 2, fix, allowed)
                if verdict.accepted:
                    verdicts[i], repaired = verdict, repaired + 1
        lost = [i for i in failed if not verdicts[i].accepted]
        if len(lost) / len(proposals) > MAX_FAILED_SHARE:
            # Flag the prompt only when the repair was tried; a repair lost to an outage
            # simplifies the part instead.
            return _Part(
                _template(templates),
                by_model=False,
                outage=outage,
                dropped=len(proposals),
                flagged=outage is None,
            )
        sentences: list[Sentence] = []
        for verdict in verdicts:
            if verdict.accepted:
                sentences.append(Sentence(text=verdict.text, citations=verdict.citations,
                                          written_by="model"))  # fmt: skip
                continue
            # The template sentence: the first fact it cited that this part was given.
            fact = next((allowed[c].fact for c in verdict.citations if c in allowed), None)
            if fact is not None and all(s.text != fact.statement for s in sentences):
                sentences.extend(_template([fact]))
        kept = len(proposals) - len(lost)
        return _Part(
            tuple(sentences), by_model=True, kept=kept, dropped=len(lost), repaired=repaired
        )

    async def _draft(self, user: str) -> DraftProposal:
        async with self._limit:
            reply = await self.model.complete(
                ModelRequest(
                    purpose=Purpose.NARRATE,
                    org_id=self.org_id,
                    run_id=self.run_id,
                    prompt_id=PROMPT_ID,
                    prompt_version=PROMPT_VERSION,
                    system=SYSTEM_PROMPT,
                    user=user,
                    response_model=DraftProposal,
                    response_schema=response_schema(DraftProposal),
                    sensitive_values=self.sensitive_values,
                )
            )
        self.provider, self.model_name = reply.provider, reply.model
        assert isinstance(reply.data, DraftProposal)  # noqa: S101  (the request's model)
        return reply.data

    async def _verify(
        self,
        name: str,
        position: int,
        attempt: int,
        proposal: SentenceProposal,
        allowed: Mapping[str, SlottedFact],
    ) -> Verdict:
        verdict = check(proposal.text, proposal.citations, allowed)
        if verdict.accepted and self.judge:
            rejected = await self._judged(verdict, allowed)
            if rejected is not None:
                raw = " ".join(proposal.text.split())
                verdict = Verdict(raw, verdict.citations, "judge", rejected)
        self.records.append(
            VerificationRecord(
                part=name,
                position=position,
                attempt=attempt,
                accepted=verdict.accepted,
                check=verdict.check,
                detail=verdict.detail,
                citations=verdict.citations,
                text=verdict.text,
            )
        )
        return verdict

    async def _judged(self, verdict: Verdict, allowed: Mapping[str, SlottedFact]) -> str | None:
        """The judge's reason to reject, or ``None``. It can only reject; if it is
        unavailable, the sentence stands on the verifier's checks (the judge is skipped)."""
        try:
            async with self._limit:
                reply = await self.model.complete(
                    ModelRequest(
                        purpose=Purpose.JUDGE,
                        org_id=self.org_id,
                        run_id=self.run_id,
                        prompt_id=JUDGE_PROMPT_ID,
                        prompt_version=JUDGE_PROMPT_VERSION,
                        system=JUDGE_SYSTEM_PROMPT,
                        user=judge_prompt(
                            verdict.text, [allowed[c].fact for c in verdict.citations]
                        ),
                        response_model=JudgeVerdict,
                        response_schema=response_schema(JudgeVerdict),
                        sensitive_values=self.sensitive_values,
                    )
                )
        except ModelUnavailable:
            return None
        assert isinstance(reply.data, JudgeVerdict)  # noqa: S101  (the request's model)
        if reply.data.accept:
            return None
        return " ".join(reply.data.reason.split())[:200] or "The judge rejected it."
