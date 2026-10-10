"""The four Phase A stages run in process: memory storage, a recording repository, a fake model."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from insightfulcell.engine.planning.language import ProposedPlan
from insightfulcell.engine.ports import (
    CachedPlan,
    CleaningRecord,
    ColumnRecord,
    ModelReply,
    ModelRequest,
    ModelUnavailable,
    PlanRecord,
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

    @property
    def identity(self) -> tuple[str, str] | None:
        return ("gemini", "gemini-3.8-flash")

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if self.fail:
            raise ModelUnavailable("timeout")
        return ModelReply(
            data=ProposedPlan.model_validate(MODEL_PLAN), provider="gemini", model="gemini-3.8-flash",  # noqa: E501
            input_tokens=5000, output_tokens=900, attempts=1,
        )  # fmt: skip


async def run_phase_a(
    tmp_path: Path, model: FakeModel, recorder: Recorder
) -> dict[str, StageResult]:
    source = SourceFile(
        version_id=uuid4(),
        bronze_key="bronze/o/x",
        sha256=hashlib.sha256(CSV).hexdigest(),
        format=FileFormat.CSV,
        bytes=len(CSV),
    )
    store = MemoryStore({source.bronze_key: CSV})
    inputs = RunInputs(dataset_name="subscribers", today="2026-10-10")
    org, run = uuid4(), uuid4()
    results: dict[str, StageResult] = {}
    keys: list[str] = []
    for planned in default_pipeline(model_identity=model.identity).planned():
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
    assert clean.needs_review  # the model's merge of N.S.W. -> NSW is lossy
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
