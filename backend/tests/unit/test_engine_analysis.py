"""Phase B's engine: the miners' facts, number formatting, the narration verifier and its
fallbacks, the report, and the engine's reading of a person's requests."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from insightfulcell.engine.analysis import facts as fmt
from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.miners import mine
from insightfulcell.engine.analysis.narrate import (
    NarrationProposal,
    narrate,
    numbers_supported,
    verify,
)
from insightfulcell.engine.analysis.prompt import SectionInput
from insightfulcell.engine.analysis.report import Preparation, build
from insightfulcell.engine.planning import requests as person_requests
from insightfulcell.engine.planning.language import (
    MinerKind,
    Provenance,
    ResolvedAnalysis,
    ResolvedColumn,
    ResolvedPlan,
    Role,
    Source,
)
from insightfulcell.engine.planning.schema import response_schema
from insightfulcell.engine.ports import ModelReply, ModelRequest, ModelUnavailable, NoModel
from insightfulcell.engine.types import Meaning
from tests.unit.engine_support import analyse, by_name, table

# --------------------------------------------------------------------------- helpers


def column(name: str, meaning: Meaning, unit: str | None = None) -> ResolvedColumn:
    role = Role.MEASURE if meaning in {Meaning.CONTINUOUS, Meaning.CURRENCY} else Role.DIMENSION
    return ResolvedColumn(
        column=name,
        display_name=name.title(),
        description="",
        unit=unit,
        meaning=meaning,
        role=role,
        source=Source.RULES,
    )


def plan_with(
    columns: list[ResolvedColumn], *analyses: tuple[MinerKind, tuple[str, ...]]
) -> ResolvedPlan:
    return ResolvedPlan(
        dataset_summary="",
        domain="",
        grain="",
        dictionary=tuple(columns),
        target=None,
        features=(),
        analyses=tuple(
            ResolvedAnalysis(
                id=f"{k.value}:{','.join(c)}",
                kind=k,
                columns=c,
                question=f"Q {k.value}?",
                priority=1,
                source=Source.RULES,
            )
            for k, c in analyses
        ),
        charts=(),
        relevance={},
        cleaning=(),
        provenance=Provenance(source="rules", schema_fingerprint="0" * 64),
    )


def fact(fid: str, statement: str, numbers: tuple[str, ...], **values: object) -> Fact:
    return Fact(
        id=fid,
        analysis_id="a",
        kind=str(values.pop("kind", "correlation")),
        columns=("x",),
        rows=40,
        method="m",
        statement=statement,
        numbers=numbers,
        values=dict(values),
    )


# --------------------------------------------------------------------------- miners


def test_a_rank_correlation_is_measured_and_put_into_words() -> None:
    rows = [(float(i), float(2 * i + (i % 3))) for i in range(30)]
    with table([("x", "DOUBLE"), ("y", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [column("x", Meaning.CONTINUOUS), column("y", Meaning.CONTINUOUS)],
            (MinerKind.RELATIONSHIP, ("x", "y")),
        )
        (finding,) = mine(con, plan).findings
    (only,) = finding.facts
    assert only.id == "F1"
    assert only.values["spearman"] == pytest.approx(0.99, abs=0.01)
    assert "rise together" in only.statement
    assert "strong" in only.statement
    assert "95% interval" in only.statement
    assert all(n in only.statement for n in only.numbers)
    assert only.tier == "strong"
    assert only.test is not None
    assert only.test.q_value < 0.01
    assert finding.chart is not None
    assert (finding.chart.kind, len(finding.chart.points)) == ("scatter", 30)


def test_groups_under_ten_rows_are_never_reported_on_their_own() -> None:
    rows = [("North", 10.0 + i % 4) for i in range(15)]
    rows += [("South", 20.0 + i % 4) for i in range(15)]
    rows += [("Tiny", 99.0) for _ in range(3)]  # three rows: pooled, then dropped (< 10)
    with table([("region", "VARCHAR"), ("sales", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [column("region", Meaning.NOMINAL), column("sales", Meaning.CURRENCY, "$")],
            (MinerKind.SEGMENT, ("region", "sales")),
        )
        (finding,) = mine(con, plan).findings
    (comparison,) = finding.facts
    assert "Tiny" not in comparison.statement
    assert comparison.statement.startswith("Average Sales is highest for South at $21.4")
    assert finding.chart is not None
    assert {p.label for p in finding.chart.points} == {"North", "South"}
    assert comparison.values["eta"] > 0.9


def test_a_trend_reports_first_last_and_peak_periods() -> None:
    rows = [(date(2025, m, d), float(m * 10)) for m in range(1, 7) for d in (1, 15)]
    with table([("day", "DATE"), ("orders", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [column("day", Meaning.DATETIME), column("orders", Meaning.CONTINUOUS)],
            (MinerKind.TREND, ("day", "orders")),
        )
        (finding,) = mine(con, plan).findings
    (trend,) = finding.facts
    assert trend.statement == (
        "Average Orders went from 10 in Jan 2025 to 60 in Jun 2025 (+500%); the highest "
        "month was Jun 2025 at 60. Across the 6 months the trend is upward (Kendall's tau 1.00)."
    )
    assert trend.tier in {"strong", "supported"}
    assert trend.labels == ("Jan 2025", "Jun 2025", "Jun 2025")


def test_unanswerable_questions_are_skipped_with_a_reason() -> None:
    with table([("x", "DOUBLE"), ("y", "DOUBLE")], [(1.0, 2.0)] * 3) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [column("x", Meaning.CONTINUOUS), column("y", Meaning.CONTINUOUS)],
            (MinerKind.RELATIONSHIP, ("x", "y")),
            (MinerKind.DISTRIBUTION, ("missing_column",)),
        )
        too_few, missing = mine(con, plan).findings
    assert too_few.skipped == "Too few rows have both X and Y."
    assert missing.skipped == "A column of this question is not in the cleaned data."
    assert not too_few.facts
    assert too_few.chart is None


def test_a_column_almost_identical_to_the_outcome_is_flagged_as_possible_leakage() -> None:
    rows = [("yes" if i % 2 else "no", "paid" if i % 2 else "open", float(i)) for i in range(40)]
    with table([("churned", "VARCHAR"), ("status", "VARCHAR"), ("age", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        plan = plan_with(
            [
                column("churned", Meaning.NOMINAL),
                column("status", Meaning.NOMINAL),
                column("age", Meaning.CONTINUOUS),
            ],
            (MinerKind.DRIVER, ("churned",)),
        )
        (finding,) = mine(con, plan).findings
    leak = next(f for f in finding.facts if f.kind == "leakage")
    assert leak.statement.startswith("Status is almost identical to Churned (yes)")
    assert "may be recorded after it" in leak.statement
    # Too few rows to check a model on held-out rows: no driver is reported.
    assert not [f for f in finding.facts if f.kind == "driver"]


@pytest.mark.parametrize(
    ("value", "meaning", "unit", "expected"),
    [
        (1234.5, Meaning.CURRENCY, "$", "$1,234"),
        (-12.5, Meaning.CURRENCY, "$", "-$12.5"),
        (0.4123, Meaning.PERCENTAGE, None, "41%"),
        (0.045, Meaning.PERCENTAGE, None, "4.5%"),
        (12.345, Meaning.CONTINUOUS, "km", "12.3 km"),
        (3.0, Meaning.CONTINUOUS, None, "3"),
        (0.04123, Meaning.CONTINUOUS, None, "0.0412"),
    ],
)
def test_values_are_written_once_the_same_way(
    value: float, meaning: Meaning, unit: str | None, expected: str
) -> None:
    assert fmt.measure(value, meaning, unit) == expected


def test_strength_words_follow_fixed_thresholds() -> None:
    assert [fmt.strength(v) for v in (0.05, -0.2, 0.42, -0.8)] == [
        "negligible",
        "weak",
        "moderate",
        "strong",
    ]


# --------------------------------------------------------------------------- narration


SECTIONS = [
    SectionInput(
        analysis_id="relationship:x,y",
        question="Is X related to Y?",
        facts=(fact("F1", "X and Y rise together (rank correlation 0.42).", ("0.42",)),),
    ),
    SectionInput(
        analysis_id="trend:d,y",
        question="How does Y change?",
        facts=(
            fact(
                "F2",
                "Average Y went from 10 in Jan 2025 to 8 in Jun 2025 (-20%).",
                ("10", "8", "-20%"),
                kind="trend",
                first=10.0,
                last=8.0,
            ),
        ),
    ),
]


def proposal(sections: dict[str, list[dict[str, object]]]) -> NarrationProposal:
    return NarrationProposal.model_validate(
        {
            "summary": [{"text": "Y fell by 20% over the period.", "citations": ["F2"]}],
            "sections": [{"analysis_id": k, "sentences": v} for k, v in sections.items()],
        }
    )


def test_numbers_must_appear_in_the_cited_facts() -> None:
    cited = [SECTIONS[0].facts[0]]
    assert numbers_supported("The link is moderate, at 0.42.", cited)
    assert not numbers_supported("The link is moderate, at 0.47.", cited)
    assert not numbers_supported("About 42% move together.", cited)  # 42% is not 0.42


def test_the_verifier_keeps_supported_sentences_and_drops_the_rest() -> None:
    narration = verify(
        proposal(
            {
                "relationship:x,y": [
                    {"text": "X and Y tend to rise together (0.42).", "citations": ["F1"]},
                    {"text": "Higher X causes higher Y.", "citations": ["F1"]},  # causal
                ],
                "trend:d,y": [
                    {"text": "Y went from 10 to 8 between January and June.", "citations": ["F2"]},
                    {"text": "Y grew steadily.", "citations": ["F2"]},  # wrong direction
                    {"text": "The link is 0.42.", "citations": ["F1"]},  # another question's
                ],
            }
        ),
        SECTIONS,
        provider="gemini",
        model="m",
    )
    # x,y lost one of two sentences (more than a third): the engine wrote it instead.
    assert narration.written_by == {"relationship:x,y": "engine", "trend:d,y": "engine"}
    assert narration.sections["relationship:x,y"][0].text == SECTIONS[0].facts[0].statement
    assert narration.source == "engine"
    assert [s.text for s in narration.summary] == ["Y fell by 20% over the period."]
    assert narration.dropped == 5


def test_a_clean_model_answer_is_kept_with_its_citations() -> None:
    narration = verify(
        proposal(
            {
                "relationship:x,y": [
                    {
                        "text": "X and Y tend to rise together, moderately (0.42).",
                        "citations": ["F1"],
                    }
                ],
                "trend:d,y": [{"text": "Y fell from 10 to 8, a 20% drop.", "citations": ["F2"]}],
            }
        ),
        SECTIONS,
    )
    assert narration.source == "model"
    assert narration.sections["trend:d,y"][0].citations == ("F2",)
    assert (narration.kept, narration.dropped) == (3, 0)


class _Narrator:
    def __init__(self, reply: NarrationProposal | None) -> None:
        self.reply = reply
        self.requests: list[ModelRequest] = []

    @property
    def identity(self) -> tuple[str, str] | None:
        return ("anthropic", "claude-sonnet-5")

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if self.reply is None:
            raise ModelUnavailable("timeout")
        return ModelReply(
            data=self.reply,
            provider="anthropic",
            model="claude-sonnet-5",
            input_tokens=1,
            output_tokens=1,
            attempts=1,
        )


async def call(model: object) -> object:
    return await narrate(
        model,  # type: ignore[arg-type]
        org_id=uuid4(),
        run_id=uuid4(),
        sections=SECTIONS,
        context={"table": "test"},
        response_schema=response_schema(NarrationProposal),
        sensitive_values=frozenset({"secret@example.com"}),
    )


async def test_without_a_model_or_when_it_fails_the_engine_writes_every_answer() -> None:
    off = await call(NoModel())
    failing = _Narrator(None)
    failed = await call(failing)
    for narration in (off, failed):
        assert narration.source == "engine"  # type: ignore[attr-defined]
    assert (off.fallback_reason, failed.fallback_reason) == ("disabled", "timeout")  # type: ignore[attr-defined]
    (request,) = failing.requests
    assert "F2" in request.user
    assert "Average Y went from 10" in request.user
    assert request.sensitive_values == frozenset({"secret@example.com"})


# --------------------------------------------------------------------------- report


def test_the_report_puts_answered_questions_first_and_names_columns() -> None:
    columns = [column("x", Meaning.CONTINUOUS), column("y", Meaning.CONTINUOUS)]
    plan = plan_with(
        columns, (MinerKind.RELATIONSHIP, ("x", "y")), (MinerKind.DISTRIBUTION, ("x",))
    )
    rows = [(float(i), float(i * i)) for i in range(20)]
    with table([("x", "DOUBLE"), ("y", "DOUBLE")], rows) as con:
        con.execute("CREATE VIEW a AS SELECT * FROM t")
        findings = mine(con, plan).findings
    findings[0].facts, findings[0].skipped = [], "skipped on purpose"
    sections = [SectionInput(f.analysis.id, f.analysis.question, tuple(f.facts)) for f in findings]
    from insightfulcell.engine.analysis.narrate import engine_narration  # noqa: PLC0415

    report = build(
        dataset_name="Shop",
        plan=plan,
        findings=findings,
        narration=engine_narration(sections, reason="disabled"),
        preparation=Preparation(
            rows_before=20, rows_after=20, applied=(), not_applied=0, reviewed_by_person=True
        ),
        rows=20,
    )
    assert report.title == "Report on Shop"
    assert [s.skipped for s in report.sections] == [None, "skipped on purpose"]
    assert report.column_names == {"x": "X", "y": "Y"}
    assert report.sections[0].columns[0].display_name == "X"
    assert report.summary
    assert report.summary[0].citations == report.sections[0].facts[:1]


# --------------------------------------------------------------------------- requests


def test_requests_name_columns_by_raw_or_display_name_and_longest_first() -> None:
    columns = [("Labour Cost", "DOUBLE"), ("Cost", "DOUBLE"), ("Team", "VARCHAR")]
    rows = [(float(i), float(i % 7), ["Red", "Blue"][i % 2]) for i in range(30)]
    with table(columns, rows) as con:
        found, profile = analyse(con, columns)
    names = {"Labour Cost": "Labour cost", "Cost": "Cost", "Team": "Team"}
    intents = person_requests.interpret(
        ["is labour cost related to cost?", "compare costs across teams", "drop everything"],
        semantics=found,
        profile=profile,
        names=names,
        target=None,
        dataset_name="d",
    )
    first, second, third = intents
    assert first.add is not None
    assert first.add.columns == ["Labour Cost", "Cost"]
    assert second.add is not None
    assert second.add.kind is MinerKind.SEGMENT
    assert second.add.columns == ["Team", "Cost"]
    assert third.not_understood
    assert "Name a column" in third.note
    assert by_name(found)["Team"].meaning is Meaning.NOMINAL


def test_requests_are_cleaned_and_capped() -> None:
    cleaned = person_requests.clean_requests(
        ["  compare\nteams ", "COMPARE TEAMS", "", *[f"r{i}" for i in range(10)]]
    )
    assert cleaned[0] == "compare teams"  # control characters become spaces
    assert len(cleaned) == person_requests.MAX_REQUESTS
    assert "COMPARE TEAMS" not in cleaned  # the same request twice counts once
