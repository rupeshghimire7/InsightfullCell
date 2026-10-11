"""Phase 5: narration with numeric slots, the sentence verifier, repair, fallbacks, the judge,
and the corrupted-narrator evaluation (every planted error must be caught, and no clean
sentence rejected)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from pydantic import BaseModel

from insightfulcell.engine.analysis.facts import Fact, FactTest, Tier
from insightfulcell.engine.analysis.narrate import (
    MAX_FAILED_SHARE,
    DraftProposal,
    JudgeVerdict,
    Narration,
    engine_narration,
    narrate,
)
from insightfulcell.engine.analysis.prompt import SectionInput
from insightfulcell.engine.analysis.verifier import check, slotted
from insightfulcell.engine.ports import ModelReply, ModelRequest, ModelUnavailable, NoModel, Purpose


def _fact(
    fid: str,
    statement: str,
    numbers: tuple[str, ...],
    *,
    kind: str = "correlation",
    labels: tuple[str, ...] = (),
    tier: Tier = "descriptive",
    effect: float = 0.0,
    effect_name: str = "spearman",
    **values: object,
) -> Fact:
    test = None
    if tier != "descriptive":
        q = {"strong": 0.001, "supported": 0.03, "inconclusive": 0.6}[tier]
        test = FactTest(
            hypothesis=f"h:{fid}", method="m", n=200, effect_name=effect_name, effect=effect,
            ci_low=None, ci_high=None, p_value=q / 2, q_value=q, size=abs(effect),
        )  # fmt: skip
    return Fact(
        id=fid, analysis_id=f"a:{fid}", kind=kind, columns=("x",), rows=200, method="m",
        statement=statement, numbers=numbers, labels=labels, values=dict(values), tier=tier,
        test=test,
    )  # fmt: skip


LINK = _fact(
    "F1",
    "Price and Sales move in opposite directions: the link is moderate (rank correlation "
    "-0.42, 95% interval -0.55 to -0.27).",
    ("-0.42", "-0.55", "-0.27"),
    tier="strong",
    effect=-0.42,
    spearman=-0.42,
)
GROUPS = _fact(
    "F2",
    "Average Sales is highest for North at $120 and lowest for South at $80, against $100 "
    "overall; the difference between groups is weak (eta 0.21).",
    ("$120", "$80", "$100", "0.21"),
    kind="group_comparison",
    labels=("North", "South"),
    tier="supported",
    effect=0.21,
    effect_name="eta",
)
TREND = _fact(
    "F3",
    "Average Sales went from 10 in Jan 2025 to 8 in Jun 2025 (-20%); the highest month was "
    "Mar 2025 at 12.",
    ("10", "8", "-20%", "12"),
    kind="trend",
    labels=("Jan 2025", "Jun 2025", "Mar 2025"),
    first=10.0,
    last=8.0,
)
UNSURE = _fact(
    "F4",
    "Region and Returns show no reliable link in this data (rank correlation 0.03).",
    ("0.03",),
    tier="inconclusive",
    effect=0.03,
)
WITHIN = _fact(
    "F5",
    "Overall, Price and Sales have a rank correlation of -0.42; within Region groups it is "
    "-0.15 (95% interval -0.2 to -0.1), with the same direction in 3 of 4 groups.",
    ("-0.42", "-0.15", "-0.2", "-0.1", "3", "4"),
    kind="conditional",
    tier="supported",
    effect=-0.15,
)
# From a live run: an inconclusive trend whose values are still exact.
FLAT = _fact(
    "F6",
    "Average Floor area m2 went from 1,524 m² in Jan 2025 to 1,520 m² in Oct 2025 (-0.3%); the "
    "highest month was Jul 2025 at 1,810 m². Across the 10 months there is no reliable upward "
    "or downward trend (Kendall's tau -0.20).",
    ("1,524 m²", "1,520 m²", "-0.3%", "1,810 m²", "10", "-0.20"),
    kind="trend",
    labels=("Jan 2025", "Oct 2025", "Jul 2025"),
    tier="inconclusive",
    effect=-0.2,
    effect_name="kendall_tau",
    first=1524.0,
    last=1520.0,
)
ALL = {f.id: slotted(f) for f in (LINK, GROUPS, TREND, UNSURE, WITHIN, FLAT)}


# --------------------------------------------------------------------------- slots


def test_numbers_become_slots_with_the_conditions_they_hold_under() -> None:
    groups = ALL["F2"]
    assert groups.statement == (
        "Average Sales is highest for North at {F2.1} and lowest for South at {F2.2}, against "
        "{F2.3} overall; the difference between groups is weak (eta {F2.4})."
    )
    assert {k: (s.value, s.conditions) for k, s in groups.slots.items()} == {
        "F2.1": ("$120", ("North",)),
        "F2.2": ("$80", ("South",)),
        "F2.3": ("$100", ()),
        "F2.4": ("0.21", ()),
    }
    assert ALL["F3"].slots["F3.4"].conditions == ("Mar 2025",)
    assert ALL["F3"].slots["F3.3"].conditions == ()  # the change, between the two periods
    assert ALL["F5"].slots["F5.2"].conditions == ("within",)
    assert "2025" in ALL["F3"].literals


CLEAN = [
    ("Price and Sales tend to move in opposite directions, a moderate link ({F1.1}).", ["F1"]),
    ("There is reliable, strong evidence that higher Price goes with lower Sales.", ["F1"]),
    ("Average Sales is highest for North at {F2.1} and lowest for South at {F2.2}.", ["F2"]),
    ("Region is only weakly associated with Sales (eta {F2.4}).", ["F2"]),
    ("Average Sales fell from {F3.1} in Jan 2025 to {F3.2} in Jun 2025 ({F3.3}).", ["F3"]),
    ("The busiest month was Mar 2025, at {F3.4}.", ["F3"]),
    ("There is no reliable link between Region and Returns ({F4.1}).", ["F4"]),
    ("Within Region groups the link is weaker, at {F5.2}.", ["F5"]),
    ("Price and Sales are associated ({F1.1}), and North leads at {F2.1}.", ["F1", "F2"]),
    # Restating an inconclusive fact's values is not claiming its trend (a live-run finding).
    ("Average Floor area m2 went from {F6.1} in Jan 2025 to {F6.2} in Oct 2025 ({F6.3}).", ["F6"]),
    ("The highest month was Jul 2025 at {F6.4}.", ["F6"]),
]


def test_clean_sentences_are_accepted_with_the_facts_numbers_filled_in() -> None:
    verdicts = [check(text, cites, ALL) for text, cites in CLEAN]
    assert [(v.check, v.detail) for v in verdicts] == [(None, None)] * len(CLEAN)
    assert verdicts[0].text == (
        "Price and Sales tend to move in opposite directions, a moderate link (-0.42)."
    )
    assert verdicts[4].text == "Average Sales fell from 10 in Jan 2025 to 8 in Jun 2025 (-20%)."


# The corrupted-narrator evaluation: each sentence plants one kind of error a narrator makes.
CORRUPTED = [
    ("Price and Sales are linked at -0.47.", ["F1"], "stray_number"),  # a wrong number
    ("Price and Sales are linked at -0.42.", ["F1"], "stray_number"),  # right, but not a slot
    ("North sells twice as much as South ({F2.1}).", ["F2"], "stray_number"),
    ("Sales fell by twenty percent in Jun 2025 ({F3.3}).", ["F3"], "stray_number"),
    ("Sales were 1,000 higher for North ({F2.1}).", ["F2"], "stray_number"),
    ("Price and Sales rise together ({F1.1}).", ["F1"], "direction"),
    ("The link between Price and Sales is positive ({F1.1}).", ["F1"], "direction"),
    ("Average Sales grew from {F3.1} in Jan 2025 to {F3.2} in Jun 2025.", ["F3"], "direction"),
    ("Sales plummeted from {F3.1} in Jan 2025 to {F3.2} in Jun 2025.", ["F3"], "strength"),
    ("Region is strongly associated with Sales (eta {F2.4}).", ["F2"], "strength"),
    ("Region has a dramatic association with Sales ({F2.4}).", ["F2"], "strength"),
    ("Price and Sales are only weakly linked ({F1.1}).", ["F1"], "strength"),
    ("Region goes with Returns ({F4.1}).", ["F4"], "strength"),  # inconclusive as a finding
    ("Floor area m2 shows a downward trend over the months ({F6.6}).", ["F6"], "strength"),
    ("Average Floor area m2 rose to {F6.2} in Oct 2025.", ["F6"], "direction"),
    ("Higher prices cause lower sales ({F1.1}).", ["F1"], "causal"),
    ("Price drives Sales down ({F1.1}).", ["F1"], "causal"),
    ("Sales are lower because of Price ({F1.1}).", ["F1"], "causal"),
    ("There is a significant link between Price and Sales ({F1.1}).", ["F1"], "causal"),
    ("Region has an impact on Sales (eta {F2.4}).", ["F2"], "causal"),
    ("Average Sales is {F2.1}.", ["F2"], "condition"),  # holds only for North
    ("Price and Sales have a rank correlation of {F5.2}.", ["F5"], "condition"),  # within
    ("Average Sales peaked at {F3.4}.", ["F3"], "condition"),  # in Mar 2025
    ("Sales vary between regions.", [], "citation"),
    ("Price and Sales are linked ({F1.1}).", ["F9"], "citation"),  # not this section's
    ("Price and Sales are linked ({F1.1}).", ["F2"], "citation"),  # slot of an uncited fact
    ("Price and Sales are linked ({F1.9}).", ["F1"], "citation"),  # no such slot
    ("Price and Sales are linked ({F1.value}).", ["F1"], "form"),
    ("Read more at https://evil.example ({F1.1}).", ["F1"], "form"),
    ("Write to help@evil.example ({F1.1}).", ["F1"], "form"),
    ("Price and Sales are linked. " * 30, ["F1"], "form"),  # too long
]


@pytest.mark.parametrize(("text", "citations", "expected"), CORRUPTED)
def test_the_verifier_catches_a_corrupted_narrator(
    text: str, citations: list[str], expected: str
) -> None:
    verdict = check(text, citations, ALL)
    assert not verdict.accepted
    assert verdict.check == expected, verdict.detail
    assert verdict.detail


def test_corrupted_narrator_evaluation_summary() -> None:
    """The measure the CI tracks: every planted error rejected, no clean sentence lost."""
    caught = sum(not check(t, c, ALL).accepted for t, c, _ in CORRUPTED)
    kept = sum(check(t, c, ALL).accepted for t, c in CLEAN)
    assert (caught / len(CORRUPTED), kept / len(CLEAN)) == (1.0, 1.0)
    assert {e for _, _, e in CORRUPTED} >= {
        "stray_number", "direction", "strength", "causal", "condition", "citation", "form"
    }  # fmt: skip


# --------------------------------------------------------------------------- the narrator


Reply = Callable[[ModelRequest], BaseModel]


@dataclass
class Scripted:
    """A model that answers by a script and records every request."""

    reply: Reply
    requests: list[ModelRequest] = field(default_factory=list)
    in_flight: int = 0
    most_in_flight: int = 0

    @property
    def identity(self) -> tuple[str, str] | None:
        return ("gemini", "gemini-3.8-flash")

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            data = self.reply(request)
        finally:
            self.in_flight -= 1
        return ModelReply(data=data, provider="gemini", model="gemini-3.8-flash",
                          input_tokens=10, output_tokens=10, attempts=1)  # fmt: skip


def draft(*sentences: tuple[str, list[str]]) -> DraftProposal:
    return DraftProposal.model_validate(
        {"sentences": [{"text": t, "citations": c} for t, c in sentences]}
    )


SECTIONS = [
    SectionInput("relationship:price,sales", "Is Price related to Sales?", (LINK, WITHIN)),
    SectionInput("segment:region,sales", "Do regions differ in Sales?", (GROUPS,)),
    SectionInput("trend:month,sales", "How do Sales change?", (TREND,)),
]
GOOD = {
    "Is Price": draft(
        ("Price and Sales move in opposite directions, a moderate link ({F1.1}).", ["F1"]),
        ("Within Region groups the link is weaker ({F5.2}).", ["F5"]),
    ),
    "Do regions": draft(("North has the highest average Sales, at {F2.1}.", ["F2"])),
    "How do": draft(("Sales fell from {F3.1} in Jan 2025 to {F3.2} in Jun 2025.", ["F3"])),
}
SUMMARY = draft(("Higher Price goes with lower Sales ({F1.1}).", ["F1"]))


def scripted(
    overrides: dict[str, DraftProposal] | None = None,
    *,
    repair: DraftProposal | None = None,
    judge: Callable[[ModelRequest], JudgeVerdict] | None = None,
) -> Scripted:
    answers = {**GOOD, **(overrides or {})}

    def reply(request: ModelRequest) -> BaseModel:
        if request.purpose is Purpose.JUDGE:
            assert judge is not None
            return judge(request)
        if "<rejected>" in request.user:
            if repair is None:
                raise ModelUnavailable("timeout")
            return repair
        if "Write the report's summary" in request.user:
            return SUMMARY
        return next(v for k, v in answers.items() if f'"{k}' in request.user)

    return Scripted(reply)


async def run(model: object, **kwargs: object) -> Narration:
    return await narrate(
        model,  # type: ignore[arg-type]
        org_id=uuid4(),
        run_id=uuid4(),
        sections=SECTIONS,
        context={"table": "sales"},
        sensitive_values=frozenset({"secret@example.com"}),
        summary_facts=kwargs.pop("summary_facts", ["F1", "F2", "F4"]),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


async def test_each_section_is_narrated_on_its_own_and_the_summary_last() -> None:
    model = scripted()
    narration = await run(model)
    assert narration.source == "model"
    assert narration.sections["segment:region,sales"][0].text == (
        "North has the highest average Sales, at $120."
    )
    assert narration.sections["segment:region,sales"][0].written_by == "model"
    assert [s.text for s in narration.summary] == ["Higher Price goes with lower Sales (-0.42)."]
    assert (narration.kept, narration.dropped, narration.repaired) == (5, 0, 0)
    assert narration.prompt_version == "narrate-v3"
    assert len(narration.records) == 5
    assert all(r.accepted for r in narration.records)
    # One call per section, then the summary: a section sees only its own facts, as slots.
    *sections, summary = model.requests
    assert len(sections) == 3
    assert all(r.purpose is Purpose.NARRATE for r in model.requests)
    segment = next(r for r in sections if "Do regions" in r.user)
    assert "{F2.1}" in segment.user
    assert "F1" not in segment.user
    assert all(r.sensitive_values == frozenset({"secret@example.com"}) for r in model.requests)
    # The summary may use only highlights the verified answers rest on (F4 is in none).
    assert '"F1"' in summary.user
    assert '"F2"' in summary.user
    assert '"F4"' not in summary.user


async def test_a_failed_sentence_is_repaired_once_with_its_failure_as_feedback() -> None:
    bad = draft(("North sells twice as much ({F2.1}).", ["F2"]))
    fixed = draft(("North has the highest average Sales ({F2.1}).", ["F2"]))
    model = scripted({"Do regions": bad}, repair=fixed)
    narration = await run(model)
    assert narration.sections["segment:region,sales"][0].text == (
        "North has the highest average Sales ($120)."
    )
    assert (narration.repaired, narration.dropped) == (1, 0)
    assert narration.rejected == {"stray_number": 1}
    (repair,) = [r for r in model.requests if "<rejected>" in r.user]
    assert "A number is written outside a slot." in repair.user
    attempts = [(r.attempt, r.accepted) for r in narration.records if r.part.startswith("seg")]
    assert attempts == [(1, False), (2, True)]


async def test_a_section_that_still_fails_is_written_from_templates_and_flagged() -> None:
    bad = draft(("Region drives Sales ({F2.4}).", ["F2"]))
    model = scripted({"Do regions": bad}, repair=bad)
    narration = await run(model)
    assert narration.written_by["segment:region,sales"] == "engine"
    assert (
        narration.sections["segment:region,sales"]
        == engine_narration(SECTIONS, reason="x").sections["segment:region,sales"]
    )
    assert narration.flagged == ("segment:region,sales",)
    assert narration.source == "mixed"
    assert not narration.simplified
    assert narration.rejected == {"causal": 2}


async def test_a_repair_lost_to_an_outage_simplifies_the_part_without_flagging_it() -> None:
    bad = draft(("Region drives Sales ({F2.4}).", ["F2"]))
    narration = await run(scripted({"Do regions": bad}))  # the repair call times out
    assert narration.written_by["segment:region,sales"] == "engine"
    assert narration.flagged == ()
    assert narration.simplified
    assert narration.retryable
    assert narration.fallback_reason == "timeout"


async def test_one_failure_under_the_limit_is_replaced_by_its_template_sentence() -> None:
    four = draft(
        ("Price and Sales move in opposite directions ({F1.1}).", ["F1"]),
        ("Higher Price goes with lower Sales ({F1.1}).", ["F1"]),
        ("The link holds within Region groups ({F5.2}).", ["F5"]),
        ("Price causes lower Sales ({F5.1}).", ["F5"]),
    )
    assert MAX_FAILED_SHARE >= 1 / 4
    narration = await run(scripted({"Is Price": four}))  # no repair: the model is out
    texts = [s.text for s in narration.sections["relationship:price,sales"]]
    assert texts[:3] == [
        "Price and Sales move in opposite directions (-0.42).",
        "Higher Price goes with lower Sales (-0.42).",
        "The link holds within Region groups (-0.15).",
    ]
    assert texts[3] == WITHIN.statement
    assert narration.sections["relationship:price,sales"][3].written_by == "engine"
    assert narration.written_by["relationship:price,sales"] == "model"


async def test_an_outage_simplifies_the_narration_and_it_can_be_retried() -> None:
    def down(_: ModelRequest) -> BaseModel:
        raise ModelUnavailable("breaker_open")

    narration = await run(Scripted(down))
    assert narration.source == "engine"
    assert narration.simplified
    assert narration.retryable
    assert narration.fallback_reason == "breaker_open"
    assert all(s.written_by == "engine" for p in narration.sections.values() for s in p)
    assert [s.citations for s in narration.summary] == [("F1",), ("F2",)]  # F4: in no answer

    def spent(_: ModelRequest) -> BaseModel:
        raise ModelUnavailable("budget_run")

    assert not (await run(Scripted(spent))).retryable  # the run's budget stays spent
    off = await run(NoModel())
    assert (off.source, off.simplified, off.retryable, off.fallback_reason) == (
        "engine", False, False, "disabled"
    )  # fmt: skip


async def test_the_judge_can_only_reject() -> None:
    def no(_: ModelRequest) -> JudgeVerdict:
        return JudgeVerdict(accept=False, reason="Not in the facts.")

    narration = await run(scripted(judge=no), judge=True)
    assert narration.judge == "on"
    assert narration.source == "engine"
    assert narration.rejected == {"judge": 5}

    def out(_: ModelRequest) -> JudgeVerdict:
        raise ModelUnavailable("timeout")

    skipped = await run(scripted(judge=out), judge=True)
    assert skipped.source == "model"  # an unavailable judge is skipped
    model = scripted()
    await run(model)
    assert all(r.purpose is not Purpose.JUDGE for r in model.requests)  # off by default


async def test_sections_are_narrated_concurrently_within_the_limit() -> None:
    model = scripted()
    await run(model, concurrency=2)
    assert model.most_in_flight == 2
