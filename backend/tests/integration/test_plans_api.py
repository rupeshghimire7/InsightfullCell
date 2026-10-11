"""Analysis Phase A through the API: the review gate, the plan, decisions, the recipe."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy.exc

from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import Phase
from insightfulcell.engine.analysis.narrate import DraftProposal
from insightfulcell.engine.ports import (
    ModelReply,
    ModelRequest,
    ModelUnavailable,
    Purpose,
    TestRecord,
)
from insightfulcell.llm.gateway import CallRecord
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.services.analysis_store import FactsChanged, RunAnalysisStore
from insightfulcell.services.llm_ledger import DbUsageLedger
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.runs_support import RecordingQueue, approve, create_dataset, start_run

pytestmark = pytest.mark.integration

MESSY = (
    Path(__file__).resolve().parents[3] / "sample_data" / "3_complex" / "field_service_jobs.csv"
).read_bytes()


@pytest.fixture
def executor(services: Services, settings: Settings) -> RunExecutor:
    return RunExecutor(
        TenantDatabase(services.engine),
        S3ObjectStore(settings),
        RecordingQueue(),
        default_pipeline(),
        settings,
    )


async def reviewed_run(
    client: httpx.AsyncClient, headers: dict[str, str], executor: RunExecutor, org_id: UUID
) -> tuple[str, str]:
    """(run id, dataset id) of a run of the messy sample, paused at the review gate."""
    dataset_id = (await create_dataset(client, headers, data=MESSY))["dataset"]["id"]
    run_id = (await start_run(client, headers, dataset_id)).json()["id"]
    assert await executor.execute(UUID(run_id), org_id, Phase.A) is Outcome.AWAITING_REVIEW
    return run_id, dataset_id


async def plan_of(
    client: httpx.AsyncClient, headers: dict[str, str], run_id: str
) -> dict[str, Any]:
    response = await client.get(f"/v1/runs/{run_id}/plan", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def decisions(plan: dict[str, Any], *, accept: bool = True) -> list[dict[str, Any]]:
    return [
        {"operation_id": op["id"], "accept": accept}
        for op in plan["cleaning"]["operations"]
        if op["decision"] == "pending"
    ]


class TestPlan:
    async def test_the_plan_is_not_ready_before_understand(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        response = await client.get(f"/v1/runs/{run_id}/plan", headers=auth(alice))
        assert response.status_code == 409
        assert response.json()["code"] == "plan-not-ready"

    async def test_a_messy_file_pauses_with_a_plan_masked_columns_and_pending_changes(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        response = await client.get(f"/v1/runs/{run_id}/plan", headers=auth(alice))
        assert response.status_code == 200
        plan = response.json()
        assert response.headers["etag"] == f'"{plan["run"]["row_version"]}"'
        assert plan["run"]["status"] == "awaiting_review"
        assert (plan["source"], plan["fallback_reason"]) == ("rules", "disabled")
        assert plan["target"]["column"] == "Complaint Raised"

        columns = {c["name"]: c for c in plan["columns"]}
        email = columns["Email"]
        assert (email["personal_data"], email["role"]) == ("email", "excluded")
        assert "categorical" not in email["profile"] or email["profile"]["categorical"] is None
        assert "@" not in str(email)
        assert columns["Parts Cost"]["meaning"] == "currency"

        card = plan["model_input"]
        assert card["card_version"] == "card-v1"
        assert "@" not in str(card)
        assert "Aroha" not in str(card)  # a customer's name

        cleaning = plan["cleaning"]
        assert (cleaning["revision"], cleaning["status"], cleaning["policy"]) == (
            1,
            "proposed",
            "review_lossy",
        )
        pending = {op["id"] for op in cleaning["operations"] if op["decision"] == "pending"}
        assert {
            "drop_duplicates:*",
            "set_invalid_to_null:Labour Hours",
            "parse_dates:Created",
        } <= pending
        labels = {op["id"]: op["label"] for op in cleaning["operations"]}
        assert labels["drop_duplicates:*"] == "Drop exact duplicate rows"

    async def test_the_recipe_downloads_as_python(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        response = await client.get(f"/v1/runs/{run_id}/recipe", headers=auth(alice))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/x-python")
        assert response.headers["content-disposition"].startswith(
            'attachment; filename="insightfulcell-recipe-'
        )
        compile(response.text, "recipe.py", "exec")
        assert "Waiting for a decision" in response.text

    async def test_other_organisations_cannot_see_a_plan(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        for path in ("plan", "recipe"):
            response = await client.get(f"/v1/runs/{run_id}/{path}", headers=auth(bob))
            assert response.status_code == 404


class TestDecide:
    async def test_guards_then_a_decision_continues_the_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, dataset_id = await reviewed_run(client, auth(alice), executor, alice.org_id)
        plan = await plan_of(client, auth(alice), run_id)
        url = f"/v1/runs/{run_id}/cleaning-plan:decide"
        version = f'"{plan["run"]["row_version"]}"'
        everything = decisions(plan)

        incomplete = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 1, "decisions": everything[1:]}
        )
        assert (incomplete.status_code, incomplete.json()["code"]) == (422, "decisions-incomplete")

        stale = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 2, "decisions": everything}
        )
        assert (stale.status_code, stale.json()["code"]) == (412, "cleaning-plan-changed")

        changed = await client.post(
            url,
            headers={**auth(alice), "If-Match": '"999"'},
            json={"cleaning_revision": 1, "decisions": everything},
        )
        assert (changed.status_code, changed.json()["code"]) == (412, "run-changed")

        viewer = Identity(user_id=uuid.uuid4(), org_id=alice.org_id, role="viewer")
        forbidden = await client.post(
            url, headers=auth(viewer), json={"cleaning_revision": 1, "decisions": everything}
        )
        assert forbidden.status_code == 403

        keep_email = [*everything, {"operation_id": "exclude_column:Email", "accept": False}]
        personal = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 1, "decisions": keep_email}
        )
        assert (personal.status_code, personal.json()["code"]) == (422, "invalid-decision")

        body = {
            "cleaning_revision": 1,
            "decisions": [
                *[
                    {**d, "accept": d["operation_id"] != "merge_categories:Technician"}
                    for d in everything
                ],
            ],
            "columns": [{"name": "Total (AUD)", "display_name": "Invoice total", "unit": "AUD"}],
            "target": "First-Time Fix",
            "disabled_features": [],
        }
        response = await client.post(url, headers={**auth(alice), "If-Match": version}, json=body)
        assert response.status_code == 200, response.text
        run = response.json()
        assert (run["status"], run["phase"]) == ("queued", "B")

        revisions = owner_sql(
            "SELECT revision, status, decided_by IS NOT NULL FROM app.cleaning_plans "
            "WHERE run_id = :id ORDER BY revision",
            id=run_id,
        )
        assert revisions == [(1, "proposed", False), (2, "approved", True)]
        technician = owner_sql(
            "SELECT o.decision, o.notice FROM app.cleaning_ops o "
            "JOIN app.cleaning_plans p ON p.id = o.plan_id "
            "WHERE p.run_id = :id AND p.revision = 2 "
            "AND o.op_id = 'merge_categories:Technician'",
            id=run_id,
        )
        assert technician == [("rejected", "Decided by a person.")]
        plans = owner_sql(
            "SELECT revision, source, plan->'target'->>'column' FROM app.analysis_plans "
            "WHERE run_id = :id AND kind = 'resolved' ORDER BY revision",
            id=run_id,
        )
        assert plans == [(1, "rules", "Complaint Raised"), (2, "person", "First-Time Fix")]
        prefs = owner_sql(
            "SELECT data FROM app.dataset_preferences WHERE dataset_id = :id", id=dataset_id
        )
        data = prefs[0][0]
        assert isinstance(data, dict)
        assert data["target"] == "First-Time Fix"
        assert data["columns"]["Total (AUD)"]["display_name"] == "Invoice total"
        types = [
            t
            for (t,) in owner_sql(
                "SELECT type FROM app.run_events WHERE run_id = :id ORDER BY seq", id=run_id
            )
        ]
        assert types[-1] == "review.approved"

        assert await executor.execute(UUID(run_id), alice.org_id, Phase.B) is Outcome.SUCCEEDED
        again = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 2, "decisions": []}
        )
        assert (again.status_code, again.json()["code"]) == (409, "run-state-conflict")

        recipe = (await client.get(f"/v1/runs/{run_id}/recipe", headers=auth(alice))).text
        assert "cleaning revision 2, approved" in recipe
        assert "Waiting for a decision" not in recipe

        # The next run of the dataset starts from the person's choices.
        next_run = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        assert (
            await executor.execute(UUID(next_run), alice.org_id, Phase.A) is Outcome.AWAITING_REVIEW
        )
        next_plan = await plan_of(client, auth(alice), next_run)
        assert next_plan["target"]["column"] == "First-Time Fix"
        total = next(d for d in next_plan["dictionary"] if d["column"] == "Total (AUD)")
        assert (total["display_name"], total["source"]) == ("Invoice total", "person")


class TestRevise:
    async def test_requests_and_removals_plan_again_and_keep_decisions(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, dataset_id = await reviewed_run(client, auth(alice), executor, alice.org_id)
        plan = await plan_of(client, auth(alice), run_id)
        dropped = next(a for a in plan["analyses"] if "Postcode" in a["columns"])
        body = {
            "cleaning_revision": 1,
            "decisions": [{"operation_id": "merge_categories:Technician", "accept": False}],
            "removed_analyses": [dropped["id"]],
            "requests": ["Is distance km related to wait days?", "make it nicer"],
        }
        url = f"/v1/runs/{run_id}/plan:revise"
        response = await client.post(
            url, headers={**auth(alice), "If-Match": f'"{plan["run"]["row_version"]}"'}, json=body
        )
        assert response.status_code == 202, response.text
        assert (response.json()["status"], response.json()["phase"]) == ("queued", "A")
        types = [t for (t,) in owner_sql(
            "SELECT type FROM app.run_events WHERE run_id = :id ORDER BY seq", id=run_id
        )]  # fmt: skip
        assert types[-1] == "review.revision_requested"
        prefs = owner_sql(
            "SELECT data FROM app.dataset_preferences WHERE dataset_id = :id", id=dataset_id
        )[0][0]
        assert isinstance(prefs, dict)
        assert prefs["requests"] == body["requests"]
        assert prefs["removed_analyses"] == [dropped["id"]]

        # Phase A plans again (ingest and profile are reused) and comes back for review.
        assert await executor.execute(UUID(run_id), alice.org_id, Phase.A) is (
            Outcome.AWAITING_REVIEW
        )
        reused = owner_sql(
            "SELECT name FROM app.run_stages WHERE run_id = :id AND reused ORDER BY position",
            id=run_id,
        )
        assert reused == [("ingest",), ("profile",)]
        revised = await plan_of(client, auth(alice), run_id)
        assert revised["revision"] == 2
        assert [(r["status"], r["source"]) for r in revised["requests"]] == [
            ("added", "rules"),
            ("not_understood", "rules"),
        ]
        assert revised["analyses"][0]["question"] == "Is Distance km related to Wait Days?"
        assert revised["analyses"][0]["source"] == "person"
        assert [a["id"] for a in revised["removed_analyses"]] == [dropped["id"]]
        assert dropped["id"] not in {a["id"] for a in revised["analyses"]}
        cleaning = revised["cleaning"]
        assert cleaning["revision"] == 2
        technician = next(
            op for op in cleaning["operations"] if op["id"] == "merge_categories:Technician"
        )
        assert (technician["decision"], technician["notice"]) == (
            "rejected",
            "Decided by a person.",
        )

    async def test_revisions_are_guarded(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        url = f"/v1/runs/{run_id}/plan:revise"
        stale = await client.post(url, headers=auth(alice), json={"cleaning_revision": 2})
        assert (stale.status_code, stale.json()["code"]) == (412, "cleaning-plan-changed")
        unknown = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 1, "removed_analyses": ["x:y"]}
        )
        assert (unknown.status_code, unknown.json()["code"]) == (422, "invalid-decision")
        personal = await client.post(
            url,
            headers=auth(alice),
            json={
                "cleaning_revision": 1,
                "decisions": [{"operation_id": "exclude_column:Email", "accept": False}],
            },
        )
        assert (personal.status_code, personal.json()["code"]) == (422, "invalid-decision")
        too_many = await client.post(
            url, headers=auth(alice), json={"cleaning_revision": 1, "requests": ["a"] * 6}
        )
        assert too_many.status_code == 422
        viewer = Identity(user_id=uuid.uuid4(), org_id=alice.org_id, role="viewer")
        forbidden = await client.post(url, headers=auth(viewer), json={"cleaning_revision": 1})
        assert forbidden.status_code == 403


class TestReport:
    async def test_the_report_cites_stored_facts_and_stays_in_its_organisation(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        early = await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))
        assert (early.status_code, early.json()["code"]) == (409, "report-not-ready")
        assert (await approve(client, auth(alice), run_id)).status_code == 200
        assert await executor.execute(UUID(run_id), alice.org_id, Phase.B) is Outcome.SUCCEEDED

        response = await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["title"] == f"Report on {report['dataset_name']}"
        assert report["narration"]["source"] == "engine"  # no model in tests
        assert report["preparation"]["reviewed_by_person"] is True
        assert report["preparation"]["rows_after"] == 23  # two exact duplicates dropped
        facts = {f["id"]: f for f in report["facts"]}
        assert facts
        for section in report["sections"]:
            for sentence in section["answer"]:
                assert sentence["citations"]
                assert set(sentence["citations"]) <= set(section["facts"]) <= set(facts)
        any_fact = next(iter(facts.values()))
        assert any_fact["columns"][0]["display_name"]
        assert any_fact["rows"] > 0
        assert "@" not in response.text  # personal data never reaches the report
        # Every fact carries its evidence; every test is in the run's test log.
        for fact in facts.values():
            assert (fact["tier"] == "descriptive") == (fact["test"] is None)
            assert len(fact["content_hash"]) == 64
            if fact["test"]:
                assert 0 <= fact["test"]["p_value"] <= fact["test"]["q_value"] <= 1
        evidence = report["evidence"]
        logged = owner_sql(
            "SELECT count(*), count(*) FILTER (WHERE passed) FROM app.test_log "
            "WHERE run_id = :id AND purpose = 'primary'",
            id=run_id,
        )
        assert logged == [(evidence["tests"], evidence["reliable"])]
        assert evidence["tests"] > 0
        assert set(report["highlights"]) <= set(facts)
        # The re-tested findings (on minimally cleaned data) show their check.
        rechecked = {
            fact_id
            for (fact_id,) in owner_sql(
                "SELECT fact_id FROM app.test_log WHERE run_id = :id AND purpose = 'sensitivity'",
                id=run_id,
            )
        }
        assert len(rechecked) == evidence["rechecked"]
        assert {f for f, fact in facts.items() if fact["check"]} == rechecked
        # Facts are searchable by the words of their statements and column names.
        found = owner_sql(
            "SELECT count(*) FROM app.run_facts WHERE run_id = :id "
            "AND search @@ plainto_tsquery('english', 'rows')",
            id=run_id,
        )
        assert found != [(0,)]

        other = await client.get(f"/v1/runs/{run_id}/report", headers=auth(bob))
        assert other.status_code == 404


class _Narrator:
    """Narrates by repeating each section's first statement with its slots; or is down."""

    def __init__(self, *, down: bool) -> None:
        self.down = down

    @property
    def identity(self) -> tuple[str, str] | None:
        return ("gemini", "gemini-3.8-flash")

    async def complete(self, request: ModelRequest) -> ModelReply:
        if self.down or request.purpose is not Purpose.NARRATE:
            raise ModelUnavailable("timeout")
        facts = json.loads(request.user.split("<facts>\n", 1)[1].split("\n</facts>", 1)[0])
        draft = DraftProposal.model_validate(
            {"sentences": [{"text": facts[0]["statement"], "citations": [facts[0]["id"]]}]}
        )
        return ModelReply(data=draft, provider="gemini", model="gemini-3.8-flash",
                          input_tokens=10, output_tokens=10, attempts=1)  # fmt: skip


class TestRenarrate:
    async def test_a_report_simplified_by_an_outage_is_narrated_again(
        self,
        client: httpx.AsyncClient,
        alice: Identity,
        bob: Identity,
        auth: AuthHeaders,
        services: Services,
        settings: Settings,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        def with_model(down: bool) -> RunExecutor:
            return RunExecutor(
                TenantDatabase(services.engine), S3ObjectStore(settings), RecordingQueue(),
                default_pipeline(), settings, model=_Narrator(down=down),
            )  # fmt: skip

        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        assert (await approve(client, auth(alice), run_id)).status_code == 200
        outcome = await with_model(down=True).execute(UUID(run_id), alice.org_id, Phase.B)
        assert outcome is Outcome.SUCCEEDED
        first = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        narration = first["narration"]
        assert (narration["source"], narration["fallback_reason"]) == ("engine", "timeout")
        assert narration["simplified"] is True
        assert narration["retryable"] is True
        assert all(s["written_by"] == "engine" for s in first["sections"][0]["answer"])

        assert (await client.post(f"/v1/runs/{run_id}/report:renarrate",
                                  headers=auth(bob))).status_code == 404  # fmt: skip
        response = await client.post(f"/v1/runs/{run_id}/report:renarrate", headers=auth(alice))
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "queued"
        stages = {s["name"]: s["status"] for s in response.json()["stages"]}
        assert stages["report"] == "pending"
        assert stages["mine"] == "succeeded"
        outcome = await with_model(down=False).execute(UUID(run_id), alice.org_id, Phase.B)
        assert outcome is Outcome.SUCCEEDED

        second = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        assert second["revision"] == first["revision"] + 1
        assert second["narration"]["simplified"] is False
        assert second["narration"]["prompt_version"] == "narrate-v3"
        assert second["sections"][0]["written_by"] == "model"
        assert [f["content_hash"] for f in second["facts"]] == [
            f["content_hash"] for f in first["facts"]
        ]  # the facts were reused, not computed again
        verdicts = owner_sql(
            "SELECT report_revision, count(*), bool_and(accepted) "
            "FROM app.verification_results WHERE run_id = :id GROUP BY report_revision",
            id=run_id,
        )
        assert verdicts == [(second["revision"], second["narration"]["sentences_kept"], True)]
        again = await client.post(f"/v1/runs/{run_id}/report:renarrate", headers=auth(alice))
        assert (again.status_code, again.json()["code"]) == (409, "narration-not-retryable")


class TestFactStore:
    async def test_a_retry_stores_nothing_new_and_different_facts_are_refused(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        services: Services,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = UUID((await start_run(client, auth(alice), dataset_id)).json()["id"])
        store = RunAnalysisStore(
            services.db, org_id=alice.org_id, run_id=run_id, dataset_id=UUID(dataset_id)
        )
        fact = {
            "id": "F1",
            "analysis_id": "relationship:a,b",
            "kind": "correlation",
            "columns": ["a", "b"],
            "rows": 40,
            "method": "Spearman rank correlation.",
            "statement": "A and B rise together (rank correlation 0.42).",
            "numbers": ["0.42"],
            "tier": "strong",
            "test": {"hypothesis": "correlation:a|b", "method": "Spearman", "effect_name":
                     "spearman", "effect": 0.42, "ci_low": 0.1, "ci_high": 0.6,
                     "p_value": 0.001, "q_value": 0.002},
            "content_hash": "a" * 64,
        }  # fmt: skip
        test = TestRecord(
            purpose="primary", hypothesis="correlation:a|b", method="Spearman",
            columns=("a", "b"), n=40, effect_name="spearman", effect=0.42, ci_low=0.1,
            ci_high=0.6, p_value=0.001, q_value=0.002, alpha=0.05, passed=True, fact_id="F1",
        )  # fmt: skip
        await store.save_findings([fact], [test])
        await store.save_findings([fact], [test])  # a retry: nothing new
        with pytest.raises(FactsChanged):
            await store.save_findings([{**fact, "content_hash": "b" * 64}], [test])
        assert owner_sql(
            "SELECT count(*) FROM app.test_log WHERE run_id = :id", id=run_id
        ) == [(1,)]  # fmt: skip
        assert owner_sql(
            "SELECT tier, q_value, hypothesis FROM app.run_facts WHERE run_id = :id", id=run_id
        ) == [("strong", 0.002, "correlation:a|b")]
        # A tested fact cannot be stored as descriptive, nor an untested one as strong.
        with pytest.raises(sqlalchemy.exc.DBAPIError, match="run_facts_tested_tier"):
            owner_sql(
                "INSERT INTO app.run_facts (org_id, run_id, fact_id, analysis_id, kind, columns, "
                "rows, method, statement, content_hash, tier) VALUES (:org, :run, 'F2', 'x', "
                "'k', '{a}', 1, 'm', 's', :hash, 'strong')",
                org=alice.org_id, run=run_id, hash="c" * 64,
            )  # fmt: skip


class TestSchemaGuarantees:
    async def test_every_tenant_table_forces_row_level_security(self, owner_sql: OwnerSql) -> None:
        rows = owner_sql(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
            "AND EXISTS (SELECT 1 FROM pg_attribute a "
            "WHERE a.attrelid = c.oid AND a.attname = 'org_id')"
        )
        names = {name for name, *_ in rows}
        assert {
            "run_columns",
            "analysis_plans",
            "cleaning_plans",
            "cleaning_ops",
            "dataset_preferences",
            "llm_calls",
            "usage_counters",
            "run_facts",
            "run_reports",
            "test_log",
        } <= names
        assert [name for name, enabled, forced in rows if not (enabled and forced)] == []

    async def test_plans_facts_reports_and_llm_calls_are_append_only(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, _ = await reviewed_run(client, auth(alice), executor, alice.org_id)
        assert (await approve(client, auth(alice), run_id)).status_code == 200
        assert await executor.execute(UUID(run_id), alice.org_id, Phase.B) is Outcome.SUCCEEDED
        tables = (
            "analysis_plans",
            "cleaning_plans",
            "cleaning_ops",
            "run_facts",
            "run_reports",
            "test_log",
        )
        for table in tables:
            with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
                owner_sql(f"DELETE FROM app.{table}")

    async def test_the_usage_ledger_counts_tokens_per_run_and_month(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        services: Services,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = UUID((await start_run(client, auth(alice), dataset_id)).json()["id"])
        ledger = DbUsageLedger(services.db)
        for attempt, outcome in ((1, "invalid"), (2, "ok")):
            await ledger.record(
                CallRecord(
                    org_id=alice.org_id,
                    run_id=run_id,
                    purpose="plan",
                    provider="gemini",
                    model="gemini-3.8-flash",
                    prompt_id="understand",
                    prompt_version="understand-v1",
                    attempt=attempt,
                    outcome=outcome,
                    input_tokens=4_000,
                    output_tokens=800,
                    cost_usd=Decimal("0.002"),
                    latency_ms=900,
                )
            )
        assert await ledger.run_input_tokens(alice.org_id, run_id) == 8_000
        used, budget = await ledger.month_usage(alice.org_id)
        assert (used, budget) == (9_600, 20_000_000)
        assert owner_sql("SELECT calls, cost_usd FROM app.usage_counters") == [
            (2, Decimal("0.004000"))
        ]
        with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
            owner_sql("UPDATE app.llm_calls SET outcome = 'ok'")
