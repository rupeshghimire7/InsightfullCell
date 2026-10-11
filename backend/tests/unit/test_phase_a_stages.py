"""The stages run in process: memory storage, a recording repository, a fake model.

Phase A plans (the engine first, then the model) and stops for review; Phase B applies the
cleaning, answers the questions and writes the report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from insightfulcell.domain.runs import Phase
from insightfulcell.engine.analysis.narrate import DraftProposal
from insightfulcell.engine.planning.language import ProposedPlan
from insightfulcell.engine.ports import (
    CachedPlan,
    CleaningRecord,
    ColumnRecord,
    ModelReply,
    ModelRequest,
    ModelUnavailable,
    PlanRecord,
    Purpose,
    ReportRecord,
    TestRecord,
)
from insightfulcell.engine.schema_card import card_json
from insightfulcell.ingestion.formats import FileFormat
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.pipeline.stage import (
    RunInputs,
    SourceFile,
    StageContext,
    StageResult,
    checkpoint_key,
)

CSV = (
    "customer_id,full_name,email,plan,state,monthly_fee,signup_date,churned\n"
    + "".join(
        f"C-{i:03d},Person Number{i},p{i}@example.com,{['Basic', 'basic', 'Premium'][i % 3]},"
        f"{['NSW', 'N.S.W.', 'VIC'][i % 3]},${10 + i}.99,2025-0{1 + i % 9}-1{i % 9},{str(i % 4 == 0).lower()}\n"  # noqa: E501
        for i in range(30)
    )
).encode()


class MemoryStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def download_file(self, key: str, dest: Path) -> None:
        await asyncio.to_thread(dest.write_bytes, self.objects[key])

    async def upload_file(self, source: Path, key: str) -> None:
        self.objects[key] = await asyncio.to_thread(source.read_bytes)


@dataclass
class Recorder:
    cached: CachedPlan | None = None
    columns: list[ColumnRecord] = field(default_factory=list)
    plans: list[PlanRecord] = field(default_factory=list)
    cleaning: list[CleaningRecord] = field(default_factory=list)

    async def save_columns(self, columns: Sequence[ColumnRecord]) -> None:
        self.columns = list(columns)

    async def find_cached_plan(
        self, schema_fingerprint: str, prompt_version: str
    ) -> CachedPlan | None:
        return self.cached

    async def save_plan(self, record: PlanRecord) -> None:
        self.plans.append(record)

    async def save_cleaning_plan(self, record: CleaningRecord) -> None:
        self.cleaning.append(record)

    # Phase B: the review is simulated by approving whatever is pending.
    facts: list[Mapping[str, Any]] = field(default_factory=list)
    tests: list[TestRecord] = field(default_factory=list)
    rechecks: list[TestRecord] = field(default_factory=list)
    reports: list[ReportRecord] = field(default_factory=list)

    async def latest_resolved_plan(self) -> Mapping[str, Any] | None:
        resolved = [p for p in self.plans if p.kind == "resolved"]
        return resolved[-1].plan if resolved else None

    async def approved_cleaning(self) -> list[Mapping[str, Any]] | None:
        if not self.cleaning:
            return None
        return [
            {**op, "decision": "accepted" if op["decision"] == "pending" else op["decision"]}
            for op in self.cleaning[-1].operations
        ]

    async def save_findings(
        self, facts: Sequence[Mapping[str, Any]], tests: Sequence[TestRecord]
    ) -> None:
        self.facts.extend(facts)
        self.tests.extend(tests)

    async def save_rechecks(self, tests: Sequence[TestRecord]) -> None:
        self.rechecks.extend(tests)

    async def save_report(self, record: ReportRecord) -> None:
        self.reports.append(record)


MODEL_PLAN = {
    "dataset_summary": "One row per subscriber of an app, with their plan and whether they left.",
    "domain": "subscription retention",
    "grain": "A customer.",
    "dictionary": [
        {
            "column": "monthly_fee",
            "display_name": "Monthly fee",
            "description": "What the customer pays each month.",
            "unit": "$",
            "meaning": "currency",
            "role": "measure",
        },
        {
            "column": "plan",
            "display_name": "Plan",
            "description": "The subscription tier.",
            "unit": "km",
            "meaning": "nominal",
            "role": "dimension",
        },
    ],
    "target": {"column": "churned", "kind": "binary", "rationale": "Whether the customer left."},
    "cleaning": [
        {
            "kind": "merge_categories",
            "column": "state",
            "value_map": [{"from_value": "N.S.W.", "to_value": "NSW"}],
            "lower_bound": None,
            "upper_bound": None,
            "strategy": None,
            "reason": "The same state written two ways.",
        },
    ],
    "features": [
        {
            "kind": "date_part",
            "columns": ["signup_date"],
            "part": "month",
            "bins": None,
            "reason": "Signup season.",
        }
    ],
    "analyses": [
        {
            "kind": "segment",
            "columns": ["plan", "churned"],
            "question": "Do customers on some plans leave more often?",
            "priority": 1,
        },
        {
            "kind": "segment",
            "columns": ["email", "churned"],
            "question": "By email?",
            "priority": 1,
        },
    ],
    "charts": [
        {
            "kind": "bar",
            "x": "plan",
            "y": None,
            "group": None,
            "aggregate": "count",
            "title": "Customers by plan",
        }
    ],
    "relevance": [{"column": "plan", "weight": 0.9}],
}


@dataclass
class FakeModel:
    fail: bool = False
    requests: list[ModelRequest] = field(default_factory=list)
    plan: Mapping[str, Any] = field(default_factory=lambda: MODEL_PLAN)
    #: Narrate by repeating the first fact's statement with its slots; off: unavailable.
    narration: bool = False

    @property
    def identity(self) -> tuple[str, str] | None:
        return ("gemini", "gemini-3.8-flash")

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if self.fail:
            raise ModelUnavailable("timeout")
        if request.purpose is Purpose.NARRATE:
            if not self.narration:
                raise ModelUnavailable("provider_error")
            facts = json.loads(request.user.split("<facts>\n", 1)[1].split("\n</facts>", 1)[0])
            first = facts[0]
            data: Any = DraftProposal.model_validate(
                {"sentences": [{"text": first["statement"], "citations": [first["id"]]}]}
            )
        else:
            data = ProposedPlan.model_validate(self.plan)
        return ModelReply(
            data=data, provider="gemini", model="gemini-3.8-flash",
            input_tokens=5000, output_tokens=900, attempts=1,
        )  # fmt: skip


async def run_phase_a(
    tmp_path: Path,
    model: FakeModel,
    recorder: Recorder,
    *,
    annotations: Mapping[str, Any] | None = None,
    cleaning_decisions: Mapping[str, bool] | None = None,
    phases: tuple[Phase, ...] = (Phase.A,),
) -> dict[str, StageResult]:
    source = SourceFile(
        version_id=uuid4(),
        bronze_key="bronze/o/x",
        sha256=hashlib.sha256(CSV).hexdigest(),
        format=FileFormat.CSV,
        bytes=len(CSV),
    )
    store = MemoryStore({source.bronze_key: CSV})
    inputs = RunInputs(
        dataset_name="subscribers",
        today="2026-10-10",
        annotations=dict(annotations or {}),
        cleaning_decisions=dict(cleaning_decisions or {}),
    )
    org, run = uuid4(), uuid4()
    results: dict[str, StageResult] = {}
    keys: list[str] = []
    for planned in default_pipeline(model_identity=model.identity).planned():
        if planned.phase not in phases:
            continue
        stage = planned.stage
        key = checkpoint_key(stage, source, keys, inputs)
        workdir = tmp_path / stage.name
        workdir.mkdir()
        ctx = StageContext(
            org, run, source, workdir, store, key, upstream=dict(results),
            inputs=inputs, repository=recorder, model=model,
        )  # fmt: skip
        results[stage.name] = await stage.run(ctx)
        keys.append(key)
    return results


async def test_the_model_plans_and_its_bad_items_are_dropped(tmp_path: Path) -> None:
    model, recorder = FakeModel(), Recorder()
    results = await run_phase_a(tmp_path, model, recorder)

    (request,) = model.requests
    assert "<engine_draft>" in request.user  # the engine planned first; the model refines
    assert "p1@example.com" in request.sensitive_values  # the guard knows the values ...
    assert "@" not in request.user  # ... and the card holds none of them
    assert "Person Number" not in request.user
    assert (
        request.response_schema["properties"]["target"]["anyOf"][0]["properties"]["column"]["enum"][
            0
        ]
        == "customer_id"
    )

    understand = results["understand"].output
    assert (understand["plan_source"], understand["provider"], understand["target"]) == (
        "model",
        "gemini",
        "churned",
    )
    assert understand["rejected"] >= 2  # the unit "km" and the analysis of an email column
    proposal, resolved = recorder.plans
    assert (proposal.kind, proposal.source, resolved.kind, resolved.source) == (
        "proposal",
        "model",
        "resolved",
        "model",
    )
    assert any("email" in r["item"] for r in resolved.rejected)
    plan = resolved.plan
    assert plan["dataset_summary"].startswith("One row per subscriber")
    fee = next(d for d in plan["dictionary"] if d["column"] == "monthly_fee")
    assert (fee["display_name"], fee["source"]) == ("Monthly fee", "model")
    assert next(d for d in plan["dictionary"] if d["column"] == "full_name")["role"] == "excluded"
    assert resolved.input_card is not None
    assert card_json(resolved.input_card) in request.user  # what was sent is what is stored

    names = {c.name: c for c in recorder.columns}
    assert names["email"].personal_data == "email"
    assert names["monthly_fee"].display_name == "Monthly fee"

    clean = results["clean_plan"]
    assert clean.needs_review  # every plan is reviewed
    (cleaning,) = recorder.cleaning
    ops = {o["id"]: o for o in cleaning.operations}
    merge = ops["merge_categories:state"]
    assert (merge["source"], merge["decision"], merge["params"]["value_map"]) == (
        "model",
        "pending",
        [["N.S.W.", "NSW"]],
    )
    assert ops["fix_category_case:plan"]["decision"] == "accepted"
    assert ops["parse_numbers:monthly_fee"]["decision"] == "accepted"
    assert cleaning.status == "proposed"


async def test_a_cached_plan_skips_the_model(tmp_path: Path) -> None:
    model = FakeModel()
    recorder = Recorder(
        cached=CachedPlan(plan=MODEL_PLAN, provider="gemini", model="gemini-3.8-flash")
    )
    results = await run_phase_a(tmp_path, model, recorder)
    assert model.requests == []
    assert results["understand"].output["plan_source"] == "cache"
    assert [p.source for p in recorder.plans] == ["cache"]


async def test_a_failing_model_falls_back_to_rules(tmp_path: Path) -> None:
    model, recorder = FakeModel(fail=True), Recorder()
    results = await run_phase_a(tmp_path, model, recorder)
    output = results["understand"].output
    assert (output["plan_source"], output["fallback_reason"], output["target"]) == (
        "rules",
        "timeout",
        "churned",
    )
    (resolved,) = recorder.plans
    assert resolved.source == "rules"
    assert results["clean_plan"].output["operations"] > 0


async def test_without_a_model_the_engine_reads_the_persons_requests(tmp_path: Path) -> None:
    model, recorder = FakeModel(fail=True), Recorder()
    requests = [
        "Are there unusual monthly fees?",
        "Compare monthly fee across plans",
        "skip the state questions",
        "make it pretty",
    ]
    results = await run_phase_a(tmp_path, model, recorder, annotations={"requests": requests})
    (resolved,) = recorder.plans
    outcomes = {o["request"]: o for o in resolved.plan["requests"]}
    added = outcomes["Are there unusual monthly fees?"]
    assert (added["status"], added["source"]) == ("added", "rules")
    assert outcomes["Compare monthly fee across plans"]["status"] == "already_planned"
    assert outcomes["skip the state questions"]["status"] == "removed"
    assert outcomes["make it pretty"]["status"] == "not_understood"
    analyses = resolved.plan["analyses"]
    assert analyses[0]["question"] == "Are there unusual values in Monthly fee?"
    assert analyses[0]["source"] == "person"
    assert not any("state" in a["columns"] for a in analyses)
    assert results["understand"].output["requests_applied"] == 2
    assert model.requests  # the model was asked (and failed): the engine took over


async def test_the_model_reads_requests_and_the_engine_covers_what_it_skipped(
    tmp_path: Path,
) -> None:
    plan = {
        **MODEL_PLAN,
        "analyses": [
            *MODEL_PLAN["analyses"],
            {
                "kind": "distribution",
                "columns": ["monthly_fee"],
                "question": "How much do customers pay each month?",
                "priority": 1,
            },
        ],
        "request_outcomes": [
            {"index": 1, "status": "added", "note": "Added a look at monthly fees."}
        ],
    }
    model, recorder = FakeModel(plan=plan), Recorder()
    removed = "segment:plan,churned"
    await run_phase_a(
        tmp_path,
        model,
        recorder,
        annotations={
            "requests": ["Show how monthly fees are spread", "skip the state questions"],
            "removed_analyses": [removed],
        },
    )
    (request,) = model.requests
    assert "<requests>" in request.user
    assert "1. Show how monthly fees are spread" in request.user
    assert removed in request.user  # the model is told what the person removed
    resolved = recorder.plans[-1]  # no proposal is cached for a person's requests
    assert [p.kind for p in recorder.plans] == ["resolved"]
    sources = [(o["status"], o["source"]) for o in resolved.plan["requests"]]
    assert sources == [("added", "model"), ("not_understood", "rules")]  # no state question
    ids = [a["id"] for a in resolved.plan["analyses"]]
    assert removed not in ids
    assert [a["id"] for a in resolved.plan["removed_analyses"]] == [removed]


async def test_a_revision_keeps_the_cleaning_decisions_already_made(tmp_path: Path) -> None:
    model, recorder = FakeModel(), Recorder()
    await run_phase_a(
        tmp_path, model, recorder, cleaning_decisions={"merge_categories:state": False}
    )
    ops = {o["id"]: o for o in recorder.cleaning[-1].operations}
    merge = ops["merge_categories:state"]
    assert (merge["decision"], merge["notice"]) == ("rejected", "Decided by a person.")


async def test_phase_b_answers_every_question_with_cited_facts(tmp_path: Path) -> None:
    model, recorder = FakeModel(fail=True), Recorder()
    results = await run_phase_a(tmp_path, model, recorder, phases=(Phase.A, Phase.B))
    assert results["clean_apply"].output["rows_after"] == 30
    mined = results["mine"].output
    assert mined["facts"] == len(recorder.facts) > 0
    # Every test is logged once, in the run's false-discovery control, linked to its fact.
    assert mined["tests"] == len(recorder.tests) > 0
    assert len({t.hypothesis for t in recorder.tests}) == len(recorder.tests)
    assert all(t.purpose == "primary" and t.q_value is not None for t in recorder.tests)
    # A hypothesis asked twice (a comparison and a benchmark of the same columns) is one row,
    # linked to the first fact that rests on it.
    logged = {t.hypothesis for t in recorder.tests}
    assert {f["test"]["hypothesis"] for f in recorder.facts if f["test"]} <= logged
    assert {t.fact_id for t in recorder.tests if t.fact_id} <= {f["id"] for f in recorder.facts}
    for fact in recorder.facts:
        assert (fact["tier"] == "descriptive") == (fact["test"] is None)
    # This fixture has no real effects: every test is inconclusive, honestly worded, and
    # nothing qualifies for the sensitivity re-test (``test_engine_evidence`` covers that).
    reliable = [f for f in recorder.facts if f["tier"] in {"strong", "supported"}]
    assert mined["reliable"] == len(reliable) == 0
    assert all("reliabl" in f["statement"] for f in recorder.facts if f["tier"] == "inconclusive")
    assert results["clean_apply"].output["lossy_applied"] > 0
    assert len(recorder.rechecks) == min(10, len(reliable))
    # The State column (NSW, N.S.W., VIC) resolved to Australian states.
    (place,) = results["geo"].output["places"]
    assert (place["scope"], place["resolved"]) == ("state:AU", True)
    assert results["rank"].output["facts"] == len(recorder.facts)
    (report,) = recorder.reports
    assert report.narration_source == "engine"  # the model is off: the engine writes
    document = report.document
    fact_ids = {f["id"] for f in recorder.facts}
    answered = [s for s in document["sections"] if not s["skipped"]]
    assert answered
    for section in answered:
        assert section["answer"], section["question"]
        for sentence in section["answer"]:
            assert sentence["citations"]
            assert set(sentence["citations"]) <= set(section["facts"]) <= fact_ids
    assert all(set(s["citations"]) <= fact_ids for s in document["summary"])
    assert all(f["content_hash"] for f in recorder.facts)
    assert "p1@example.com" not in str(document)  # personal data never reaches the report


async def test_phase_b_narrates_each_question_and_keeps_every_verdict(tmp_path: Path) -> None:
    model, recorder = FakeModel(narration=True), Recorder()
    results = await run_phase_a(tmp_path, model, recorder, phases=(Phase.A, Phase.B))
    (report,) = recorder.reports
    narration = report.document["narration"]
    # Each statement, sent back with its slots, passes the verifier and is filled in again.
    rejected = [(v.check, v.detail, v.text) for v in report.verification if not v.accepted]
    assert not rejected, rejected
    # Every answer is the model's. The summary is too when a ranked highlight is cited in an
    # answer; otherwise it is written from the highlights' own statements.
    cited = {c for s in report.document["sections"] for x in s["answer"] for c in x["citations"]}
    by_model = bool(cited & set(report.document["highlights"]))
    assert all(s["written_by"] == "model" for s in report.document["sections"] if s["answer"])
    assert report.narration_source == narration["source"] == ("model" if by_model else "mixed")
    assert narration["prompt_version"] == report.prompt_version == "narrate-v3"
    assert not narration["simplified"]
    assert narration["sentences_dropped"] == 0
    statements = {f["id"]: f["statement"] for f in recorder.facts}
    for section in report.document["sections"]:
        for sentence in section["answer"]:
            assert sentence["written_by"] == "model"
            assert sentence["text"] == statements[sentence["citations"][0]]
    narrated = [r for r in model.requests if r.purpose is Purpose.NARRATE]
    answered = [s for s in report.document["sections"] if not s["skipped"]]
    assert len(narrated) == len(answered) + by_model  # one call per question, the summary last
    assert len(report.verification) == results["report"].output["sentences_kept"]
    assert all(v.accepted and v.attempt == 1 for v in report.verification)
