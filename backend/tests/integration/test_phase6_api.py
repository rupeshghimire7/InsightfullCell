"""Phase 6 through the API: checked charts and maps, the dashboard, the HTML export and
drill-downs (exploratory sub-runs), with their guards and tenancy."""

from __future__ import annotations

import random
import re
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import Phase
from insightfulcell.engine.analysis.report import EXPLORATORY_NOTE
from insightfulcell.exports.html import CONTENT_SECURITY_POLICY
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.runs_support import RecordingQueue, approve, create_dataset, start_run

pytestmark = pytest.mark.integration

MESSY = (
    Path(__file__).resolve().parents[3] / "sample_data" / "3_complex" / "field_service_jobs.csv"
).read_bytes()
REGIONS = ("North", "South", "East", "West")


def sales_csv(rows: int = 240) -> bytes:
    """Sales by region and channel, with a planted difference: North sells more."""
    rnd = random.Random(17)
    lines = ["order_id,region,channel,visits,sales"]
    for i in range(rows):
        region = REGIONS[i % 4]
        channel = rnd.choice(["Online", "Store"])
        visits = round(rnd.gauss(10, 3), 2)
        sales = round(100 + 6 * visits + (60 if region == "North" else 0) + rnd.gauss(0, 12), 2)
        lines.append(f"O{i:04d},{region},{channel},{visits},{sales}")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def executor(services: Services, settings: Settings) -> RunExecutor:
    return RunExecutor(
        TenantDatabase(services.engine),
        S3ObjectStore(settings),
        RecordingQueue(),
        default_pipeline(),
        settings,
    )


async def finished(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    executor: RunExecutor,
    org_id: UUID,
    data: bytes,
) -> str:
    """The id of a run of ``data`` that went through review and finished."""
    dataset_id = (await create_dataset(client, headers, data=data))["dataset"]["id"]
    run_id: str = (await start_run(client, headers, dataset_id)).json()["id"]
    assert await executor.execute(UUID(run_id), org_id, Phase.A) is Outcome.AWAITING_REVIEW
    early = await client.get(f"/v1/runs/{run_id}/dashboard", headers=headers)
    assert (early.status_code, early.json()["code"]) == (409, "report-not-ready")
    export = await client.get(f"/v1/runs/{run_id}/report.html", headers=headers)
    assert (export.status_code, export.json()["code"]) == (409, "report-not-ready")
    assert (await approve(client, headers, run_id)).status_code == 200
    assert await executor.execute(UUID(run_id), org_id, Phase.B) is Outcome.SUCCEEDED
    return run_id


async def explore(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/v1/runs/{run_id}/drilldowns",
        json=body,
        headers={**headers, "Idempotency-Key": key or f"drill-{uuid.uuid4().hex}"},
    )


class TestDashboardAndExport:
    async def test_charts_and_maps_are_checked_and_the_dashboard_cites_the_report(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, MESSY)
        report = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        assert report["has_dashboard"] is True
        assert report["visuals"]["claims"] > 0
        assert report["visuals"]["withheld"] == 0
        facts = {f["id"]: f for f in report["facts"]}
        charted = [s["chart"] for s in report["sections"] if s["chart"]]
        assert charted
        for chart in charted:
            assert chart["check"] == {"claims": len(chart["points"]), "verified": True}
            assert chart["vega_lite"]["$schema"].startswith("https://vega.github.io/schema/")
            assert len(chart["vega_lite"]["data"]["values"]) == len(chart["points"])
        # The state column is placed on a map; with 23 rows most states are hatched.
        maps = [m for s in report["sections"] for m in s["maps"]]
        assert maps
        state_map = maps[0]
        assert state_map["level"] == "state"
        assert state_map["country"] == "AU"
        assert state_map["check"]["verified"] is True
        shown = [r for r in state_map["regions"] if r["status"] != "no_rows"]
        assert shown
        for region in state_map["regions"]:
            if region["status"] != "value":
                assert region["value"] is None
                assert region["n"] is None  # small areas carry no number
            assert region["path"].startswith("M")

        response = await client.get(f"/v1/runs/{run_id}/dashboard", headers=auth(alice))
        assert response.status_code == 200, response.text
        board = response.json()
        assert board["revision"] == report["revision"]
        assert board["exploratory"] is False
        assert board["glance"]["rows"] == report["understanding"]["rows"]
        cited = {f["id"] for f in board["facts"]}
        assert cited <= set(facts)
        for tile in board["tiles"]:
            assert tile["fact_id"] in cited
            # A tile's number is one of its fact's own, as the fact writes it.
            assert tile["value"] in facts[tile["fact_id"]]["statement"]
        for item in board["insights"] + board["gallery"]:
            assert item["chart"]["check"]["verified"] is True
            assert set(item["facts"]) <= cited
        assert board["maps"]
        sections = {s["analysis_id"]: s for s in report["sections"]}
        for question in board["questions"]:
            assert question["answer"] == sections[question["analysis_id"]]["answer"]
        assert "@" not in response.text  # personal data never reaches the dashboard

        assert (
            await client.get(f"/v1/runs/{run_id}/dashboard", headers=auth(bob))
        ).status_code == 404

    async def test_the_report_downloads_as_one_file_with_no_scripts(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, MESSY)
        response = await client.get(f"/v1/runs/{run_id}/report.html", headers=auth(alice))
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/html")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="report-sales-')
        assert disposition.endswith(f'-{run_id[:8]}.html"')
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        html = response.text
        assert html.startswith("<!doctype html>")
        assert "<script" not in html.lower()
        assert " on" + "load=" not in html.lower()
        # Nothing is loaded from anywhere: no external sources, links or styles.
        assert not re.search(r"""(src|href|url)\s*[=(]\s*["']?(https?:)?//""", html)
        report = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        for fact in report["facts"]:
            assert f'id="fact-{fact["id"]}"' in html
        assert "<svg" in html
        body = html.split("</style>", 1)[1]
        assert "@" not in body  # personal data (emails) never reaches the export
        assert "example.com" not in body
        other = await client.get(f"/v1/runs/{run_id}/report.html", headers=auth(bob))
        assert other.status_code == 404

    async def test_a_report_written_before_dashboards_has_none(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, MESSY)
        owner_sql(
            "INSERT INTO app.run_reports (org_id, run_id, revision, document, narration_source) "
            "SELECT org_id, run_id, revision + 1, document, narration_source "
            "FROM app.run_reports WHERE run_id = :id",
            id=run_id,
        )
        response = await client.get(f"/v1/runs/{run_id}/dashboard", headers=auth(alice))
        assert (response.status_code, response.json()["code"]) == (409, "dashboard-not-available")
        report = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        assert report["has_dashboard"] is False
        export = await client.get(f"/v1/runs/{run_id}/report.html", headers=auth(alice))
        assert export.status_code == 200  # the export works without a dashboard


class TestDrilldowns:
    async def test_a_subset_is_analysed_again_and_labelled_exploratory(  # noqa: PLR0915
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, sales_csv())
        report = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        # The comparison of regions is explorable: its bars name their region.
        bars = next(
            s["chart"]
            for s in report["sections"]
            if s["chart"] and s["chart"]["drill_column"] == "region"
        )
        keys = {p["key"] for p in bars["points"]}
        assert "North" in keys

        key = f"drill-{uuid.uuid4().hex}"
        body = {"column": "region", "values": ["North"]}
        response = await explore(client, auth(alice), run_id, body, key=key)
        assert response.status_code == 202, response.text
        child = response.json()
        assert response.headers["location"] == f"/v1/runs/{child['id']}"
        assert (child["status"], child["phase"]) == ("queued", "B")
        assert child["drilldown"] == {
            "parent_run_id": run_id,
            "column": "region",
            "values": ["North"],
        }
        stages = {s["name"]: s for s in child["stages"]}
        for name in ("ingest", "profile", "understand", "clean_plan"):
            assert (stages[name]["status"], stages[name]["reused"]) == ("succeeded", True)
        assert stages["report"]["status"] == "pending"
        # A replay returns the same run; the same key for another subset is refused.
        again = await explore(client, auth(alice), run_id, body, key=key)
        assert (again.status_code, again.json()["id"]) == (202, child["id"])
        other = await explore(client, auth(alice), run_id, {**body, "values": ["South"]}, key=key)
        assert other.status_code == 422
        assert other.json()["code"] == "idempotency-key-reused"

        outcome = await executor.execute(UUID(child["id"]), alice.org_id, Phase.B)
        assert outcome is Outcome.SUCCEEDED
        sub = (await client.get(f"/v1/runs/{child['id']}/report", headers=auth(alice))).json()
        assert sub["scope"] == {
            "parent_run_id": run_id,
            "column": "region",
            "display_name": sub["scope"]["display_name"],
            "values": ["North"],
            "rows": 60,
        }
        assert sub["title"].startswith("Exploring ")
        assert sub["preparation"]["rows_after"] == 60
        assert sub["method_notes"][0] == EXPLORATORY_NOTE
        assert sub["facts"]
        assert all(f["exploratory"] for f in sub["facts"])
        assert not any(f["exploratory"] for f in report["facts"])
        stored = owner_sql(
            "SELECT DISTINCT provenance -> 'subset' ->> 'column', "
            "(provenance ->> 'exploratory')::boolean FROM app.run_facts WHERE run_id = :id",
            id=child["id"],
        )
        assert stored == [("region", True)]
        board = (await client.get(f"/v1/runs/{child['id']}/dashboard", headers=auth(alice))).json()
        assert board["exploratory"] is True
        assert board["scope"]["values"] == ["North"]
        # The parent is unchanged and lists its exploration.
        listed = await client.get(f"/v1/runs?parent_run_id={run_id}", headers=auth(alice))
        assert [r["id"] for r in listed.json()["items"]] == [child["id"]]
        assert listed.json()["items"][0]["drilldown"]["values"] == ["North"]
        # The dataset's latest analysis is still the full one, not the exploration.
        datasets = (await client.get("/v1/datasets", headers=auth(alice))).json()["items"]
        mine = next(d for d in datasets if d["id"] == child["dataset_id"])
        assert mine["latest_run"]["id"] == run_id
        again_parent = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        assert again_parent["facts"] == report["facts"]
        # Events never carry the values kept (they are cell values).
        events = owner_sql(
            "SELECT data::text FROM app.run_events WHERE run_id = :id", id=child["id"]
        )
        assert not any("North" in str(data) for (data,) in events)

        # An exploration cannot be narrowed further; other organisations see nothing.
        nested = await explore(client, auth(alice), child["id"], body)
        assert (nested.status_code, nested.json()["code"]) == (409, "drilldown-not-allowed")
        hidden = await explore(client, auth(bob), run_id, body)
        assert hidden.status_code == 404
        listed_bob = await client.get(f"/v1/runs?parent_run_id={run_id}", headers=auth(bob))
        assert listed_bob.json()["items"] == []

    async def test_guards(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, sales_csv(120))
        viewer = Identity(user_id=alice.user_id, org_id=alice.org_id, role="viewer")
        body = {"column": "region", "values": ["North"]}
        forbidden = await explore(client, auth(viewer), run_id, body)
        assert forbidden.status_code == 403
        for bad, code in (
            ({"column": "sales", "values": ["1"]}, "invalid-drilldown"),  # a measure
            ({"column": "no_such_column", "values": ["x"]}, "invalid-drilldown"),
            ({"column": "order_id", "values": ["O0001"]}, "invalid-drilldown"),  # an identifier
        ):
            response = await explore(client, auth(alice), run_id, bad)
            assert (response.status_code, response.json()["code"]) == (422, code), bad
        invalid: list[dict[str, Any]] = [
            {"column": "region", "values": []},
            {"column": "region", "values": [f"v{i}" for i in range(21)]},
            {"column": "region", "values": ["x" * 201]},
            {"column": "region", "values": ["North"], "extra": 1},
        ]
        for wrong in invalid:
            response = await explore(client, auth(alice), run_id, wrong)
            assert response.status_code == 422, wrong

        # A run that has not finished cannot be explored.
        dataset_id = (await create_dataset(client, auth(alice), data=sales_csv(60)))["dataset"][
            "id"
        ]
        queued = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        response = await explore(client, auth(alice), queued, body)
        assert (response.status_code, response.json()["code"]) == (409, "drilldown-not-allowed")

        # A run made by an earlier version of the pipeline can be neither explored nor
        # narrated again (its checkpoints belong to other stages).
        owner_sql("UPDATE app.runs SET code_version = '0.0.0+old' WHERE id = :id", id=run_id)
        response = await explore(client, auth(alice), run_id, body)
        assert (response.status_code, response.json()["code"]) == (409, "report-outdated")
        response = await client.post(f"/v1/runs/{run_id}/report:renarrate", headers=auth(alice))
        assert (response.status_code, response.json()["code"]) == (409, "report-outdated")

    async def test_a_subset_with_too_few_rows_fails_with_a_reason(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
    ) -> None:
        run_id = await finished(client, auth(alice), executor, alice.org_id, sales_csv(120))
        response = await explore(
            client, auth(alice), run_id, {"column": "region", "values": ["Atlantis"]}
        )
        assert response.status_code == 202, response.text
        child = response.json()["id"]
        assert await executor.execute(UUID(child), alice.org_id, Phase.B) is Outcome.FAILED
        run = (await client.get(f"/v1/runs/{child}", headers=auth(alice))).json()
        assert run["failure"]["code"] == "subset-too-small"
        assert "Atlantis" not in run["failure"]["detail"]
        assert "30" in run["failure"]["detail"]
