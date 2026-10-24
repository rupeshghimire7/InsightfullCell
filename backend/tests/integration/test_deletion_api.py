"""Deleting runs and datasets (ADR-037): the rows go at once, the files after a grace period,
nothing deleted is shown again, and a running analysis is never pulled from under its worker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest

from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import Phase
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.queue.procrastinate_queue import ProcrastinateQueue, create_procrastinate_app
from insightfulcell.services.maintenance import Maintenance
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import BUCKET, AuthHeaders, OwnerSql
from tests.integration.runs_support import RecordingQueue, create_dataset, start_run
from tests.integration.test_phase6_api import explore, finished, sales_csv

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration

#: Every table that holds rows of a run, and the column naming the run.
RUN_TABLES = (
    ("runs", "id"),
    ("run_stages", "run_id"),
    ("run_events", "run_id"),
    ("run_columns", "run_id"),
    ("analysis_plans", "run_id"),
    ("cleaning_plans", "run_id"),
    ("llm_calls", "run_id"),
    ("run_facts", "run_id"),
    ("run_reports", "run_id"),
    ("test_log", "run_id"),
    ("verification_results", "run_id"),
)


@pytest.fixture
def executor(services: Services, settings: Settings) -> RunExecutor:
    return RunExecutor(
        TenantDatabase(services.engine),
        S3ObjectStore(settings),
        RecordingQueue(),
        default_pipeline(),
        settings,
    )


@pytest.fixture
async def queue(settings: Settings) -> AsyncIterator[ProcrastinateQueue]:
    real = ProcrastinateQueue(create_procrastinate_app(settings))
    await real.open()
    try:
        yield real
    finally:
        await real.close()


@pytest.fixture
def maintenance(services: Services, settings: Settings, queue: ProcrastinateQueue) -> Maintenance:
    db = TenantDatabase(services.engine)
    store = S3ObjectStore(settings)
    executor = RunExecutor(db, store, queue, default_pipeline(), settings)
    return Maintenance(db, store, queue, executor, settings)


def rows_left(owner_sql: OwnerSql, run_ids: list[str]) -> dict[str, int]:
    """Rows of these runs in every table that can hold one (as the owner: no RLS)."""
    left = {}
    for table, column in RUN_TABLES:
        ((count,),) = owner_sql(
            f"SELECT count(*) FROM app.{table} WHERE {column}::text = ANY (:ids)",
            ids=run_ids,
        )
        left[table] = int(str(count))
    return {table: count for table, count in left.items() if count}


def keys_under(raw_s3: S3Client, prefix: str) -> list[str]:
    listing = raw_s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [item["Key"] for item in listing.get("Contents", [])]


def purges(owner_sql: OwnerSql, org_id: UUID) -> dict[str, Any]:
    return {
        str(key): (is_prefix, after)
        for key, is_prefix, after in owner_sql(
            "SELECT key, is_prefix, purge_after FROM app.storage_purges WHERE org_id = :org",
            org=org_id,
        )
    }


def due_now(owner_sql: OwnerSql) -> None:
    owner_sql("UPDATE app.storage_purges SET purge_after = now() - interval '1 second'")


class TestDeleteRun:
    async def test_a_finished_run_and_its_explorations_are_gone_at_once(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        headers = auth(alice)
        run_id = await finished(client, headers, executor, alice.org_id, sales_csv())
        child = await explore(client, headers, run_id, {"column": "region", "values": ["North"]})
        child_id = child.json()["id"]
        assert await executor.execute(UUID(child_id), alice.org_id, Phase.B) is Outcome.SUCCEEDED
        dataset_id = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()["dataset_id"]
        assert rows_left(owner_sql, [run_id]).keys() >= {"runs", "run_facts", "run_reports"}
        gold = f"gold/{alice.org_id}/{run_id}/"
        assert keys_under(raw_s3, gold)

        # Another organisation cannot see it, and a viewer may not delete it.
        assert (await client.delete(f"/v1/runs/{run_id}", headers=auth(bob))).status_code == 404
        viewer = Identity(user_id=alice.user_id, org_id=alice.org_id, role="viewer")
        assert (await client.delete(f"/v1/runs/{run_id}", headers=auth(viewer))).status_code == 403

        response = await client.delete(f"/v1/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["run_ids"]) == {run_id, child_id}
        removed_after = datetime.fromisoformat(body["files_removed_after"])
        assert removed_after - datetime.now(UTC) > timedelta(days=6)

        # Gone from the database at once, and never shown again.
        assert rows_left(owner_sql, [run_id, child_id]) == {}
        for gone in (run_id, child_id):
            assert (await client.get(f"/v1/runs/{gone}", headers=headers)).status_code == 404
            report = await client.get(f"/v1/runs/{gone}/report", headers=headers)
            assert report.status_code == 404
        listed = (await client.get("/v1/runs", headers=headers)).json()["items"]
        assert {r["id"] for r in listed}.isdisjoint({run_id, child_id})
        dataset = (await client.get(f"/v1/datasets/{dataset_id}", headers=headers)).json()
        assert dataset["dataset"]["latest_run"] is None
        assert (await client.delete(f"/v1/runs/{run_id}", headers=headers)).status_code == 404

        # The files stay for the grace period, scheduled by run.
        scheduled = purges(owner_sql, alice.org_id)
        assert set(scheduled) == {gold, f"gold/{alice.org_id}/{child_id}/"}
        assert all(is_prefix for is_prefix, _ in scheduled.values())
        assert keys_under(raw_s3, gold)

    async def test_a_running_run_is_refused_until_it_has_stopped(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        headers = auth(alice)
        dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
        run_id = (await start_run(client, headers, dataset_id)).json()["id"]
        owner_sql("UPDATE app.runs SET status = 'running' WHERE id = :id", id=run_id)

        refused = await client.delete(f"/v1/runs/{run_id}", headers=headers)
        assert (refused.status_code, refused.json()["code"]) == (409, "still-running")
        assert rows_left(owner_sql, [run_id]).get("runs") == 1

        # Cancelled, but its worker is still finishing a step: still refused.
        owner_sql(
            "UPDATE app.runs SET status = 'cancelled', finished_at = now() WHERE id = :id",
            id=run_id,
        )
        owner_sql(
            "UPDATE procrastinate.procrastinate_jobs SET status = 'doing' WHERE lock = :id",
            id=run_id,
        )
        refused = await client.delete(f"/v1/runs/{run_id}", headers=headers)
        assert refused.status_code == 409
        busy = await client.delete(f"/v1/datasets/{dataset_id}", headers=headers)
        assert (busy.status_code, busy.json()["code"]) == (409, "still-running")

        owner_sql(
            "UPDATE procrastinate.procrastinate_jobs SET status = 'succeeded' WHERE lock = :id",
            id=run_id,
        )
        assert (await client.delete(f"/v1/runs/{run_id}", headers=headers)).status_code == 200

    async def test_a_queued_run_is_deleted_with_its_waiting_job(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        headers = auth(alice)
        dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
        run_id = (await start_run(client, headers, dataset_id)).json()["id"]
        # No worker is running in this test: the run says so instead of waiting silently.
        shown = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()
        assert (shown["status"], shown["waiting_for_worker"]) == ("queued", True)
        assert owner_sql(
            "SELECT status FROM procrastinate.procrastinate_jobs WHERE lock = :id", id=run_id
        ) == [("todo",)]

        assert (await client.delete(f"/v1/runs/{run_id}", headers=headers)).status_code == 200
        assert rows_left(owner_sql, [run_id]) == {}
        assert not owner_sql(
            "SELECT 1 FROM procrastinate.procrastinate_jobs WHERE lock = :id", id=run_id
        )

    async def test_a_live_worker_means_the_run_is_not_waiting_for_one(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        headers = auth(alice)
        dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
        run_id = (await start_run(client, headers, dataset_id)).json()["id"]
        owner_sql(
            "INSERT INTO procrastinate.procrastinate_workers (last_heartbeat) "
            "VALUES (now() - interval '5 minutes')"
        )
        stale = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()
        assert stale["waiting_for_worker"] is True
        owner_sql("INSERT INTO procrastinate.procrastinate_workers (last_heartbeat) VALUES (now())")
        live = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()
        assert live["waiting_for_worker"] is False


class TestDeleteDataset:
    async def test_the_dataset_and_everything_made_from_it_are_gone_at_once(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        headers = auth(alice)
        run_id = await finished(client, headers, executor, alice.org_id, sales_csv())
        run = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()
        dataset_id, version_id = run["dataset_id"], run["dataset_version_id"]
        # A second, untouched dataset stays.
        other = (await create_dataset(client, headers))["dataset"]["id"]
        (bronze_key,) = owner_sql(
            "SELECT bronze_key FROM app.dataset_versions WHERE id = :id", id=version_id
        )[0]
        (staging_key,) = owner_sql(
            "SELECT u.staging_key FROM app.uploads u JOIN app.dataset_versions v "
            "ON v.upload_id = u.id WHERE v.id = :id",
            id=version_id,
        )[0]

        assert (
            await client.delete(f"/v1/datasets/{dataset_id}", headers=auth(bob))
        ).status_code == 404
        viewer = Identity(user_id=alice.user_id, org_id=alice.org_id, role="viewer")
        forbidden = await client.delete(f"/v1/datasets/{dataset_id}", headers=auth(viewer))
        assert forbidden.status_code == 403

        response = await client.delete(f"/v1/datasets/{dataset_id}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["run_ids"] == [run_id]

        assert rows_left(owner_sql, [run_id]) == {}
        for table, column in (
            ("datasets", "id"),
            ("dataset_versions", "dataset_id"),
            ("dataset_preferences", "dataset_id"),
            ("analysis_plans", "dataset_id"),
        ):
            assert not owner_sql(
                f"SELECT 1 FROM app.{table} WHERE {column} = :id",
                id=dataset_id,
            ), table
        assert not owner_sql("SELECT 1 FROM app.uploads WHERE staging_key = :k", k=staging_key)
        assert (await client.get(f"/v1/datasets/{dataset_id}", headers=headers)).status_code == 404
        listed = (await client.get("/v1/datasets", headers=headers)).json()["items"]
        assert [d["id"] for d in listed] == [other]
        runs = (await client.get("/v1/runs", headers=headers)).json()["items"]
        assert runs == []

        assert set(purges(owner_sql, alice.org_id)) == {
            f"gold/{alice.org_id}/{run_id}/",
            f"silver/{alice.org_id}/{version_id}/",
            str(bronze_key),
            str(staging_key),
        }


class TestPurge:
    async def test_files_leave_storage_once_the_grace_period_is_over(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        headers = auth(alice)
        run_id = await finished(client, headers, executor, alice.org_id, sales_csv())
        run = (await client.get(f"/v1/runs/{run_id}", headers=headers)).json()
        bronze = f"bronze/{alice.org_id}/"
        prefixes = (
            f"gold/{alice.org_id}/{run_id}/",
            f"silver/{alice.org_id}/{run['dataset_version_id']}/",
        )
        assert all(keys_under(raw_s3, p) for p in prefixes)
        assert keys_under(raw_s3, bronze)
        await client.delete(f"/v1/datasets/{run['dataset_id']}", headers=headers)

        assert await maintenance.purge_deleted_files() == 0  # not due yet
        assert all(keys_under(raw_s3, p) for p in prefixes)

        due_now(owner_sql)
        assert await maintenance.purge_deleted_files() == 4
        assert not any(keys_under(raw_s3, p) for p in prefixes)
        assert keys_under(raw_s3, bronze) == []
        assert purges(owner_sql, alice.org_id) == {}

    async def test_a_file_uploaded_again_before_the_purge_is_kept(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        headers = auth(alice)
        data = sales_csv(80)
        first = await create_dataset(client, headers, data=data)
        await client.delete(f"/v1/datasets/{first['dataset']['id']}", headers=headers)

        again = await create_dataset(client, headers, data=data)
        assert again["duplicate"] is False
        assert again["dataset"]["id"] != first["dataset"]["id"]
        bronze_key = f"bronze/{alice.org_id}/{again['version']['sha256']}"
        assert bronze_key not in purges(owner_sql, alice.org_id)

        due_now(owner_sql)
        await maintenance.purge_deleted_files()
        assert keys_under(raw_s3, bronze_key) == [bronze_key]
        run_id = (await start_run(client, headers, again["dataset"]["id"])).json()["id"]
        outcome = await executor.execute(UUID(run_id), alice.org_id, Phase.A)
        assert outcome is Outcome.AWAITING_REVIEW

    async def test_a_purge_that_fails_is_tried_again(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers = auth(alice)
        dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
        await client.delete(f"/v1/datasets/{dataset_id}", headers=headers)
        due_now(owner_sql)
        scheduled = len(purges(owner_sql, alice.org_id))

        async def unreachable(self: S3ObjectStore, key: str) -> None:
            raise ConnectionError("storage is down")

        monkeypatch.setattr(S3ObjectStore, "delete", unreachable)
        monkeypatch.setattr(S3ObjectStore, "delete_prefix", unreachable)
        assert await maintenance.purge_deleted_files() == 0
        assert len(purges(owner_sql, alice.org_id)) == scheduled
        monkeypatch.undo()
        assert await maintenance.purge_deleted_files() == scheduled


def count(owner_sql: OwnerSql, sql: str, **params: object) -> int:
    ((value,),) = owner_sql(sql, **params)
    return int(str(value))


#: Every tenant table that holds a workspace's own rows.
WORKSPACE_TABLES = (
    "datasets",
    "dataset_versions",
    "dataset_preferences",
    "uploads",
    "runs",
    "llm_calls",
    "idempotency_keys",
    "usage_counters",
    "user_activity",
)


class TestDeleteWorkspace:
    """ "Delete my account" (ADR-043): the API side erases the whole workspace."""

    async def test_everything_goes_at_once_and_the_files_at_the_next_sweep(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        executor: RunExecutor,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        headers = auth(alice)
        run_id = await finished(client, headers, executor, alice.org_id, sales_csv())
        await create_dataset(client, headers)  # a second dataset, never analysed
        bobs = (await create_dataset(client, auth(bob)))["dataset"]["id"]
        assert keys_under(raw_s3, f"bronze/{alice.org_id}/")
        assert keys_under(raw_s3, f"gold/{alice.org_id}/{run_id}/")

        # Only the workspace's owner may erase it.
        analyst = Identity(user_id=alice.user_id, org_id=alice.org_id, role="analyst")
        assert (await client.delete("/v1/me", headers=auth(analyst))).status_code == 403

        response = await client.delete("/v1/me", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert run_id in body["run_ids"]
        assert datetime.fromisoformat(body["files_removed_after"]) <= datetime.now(UTC)

        assert rows_left(owner_sql, [run_id]) == {}
        for table in WORKSPACE_TABLES:
            assert (
                count(
                    owner_sql,
                    f"SELECT count(*) FROM app.{table} WHERE org_id = :org",
                    org=alice.org_id,
                )
                == 0
            ), table
        assert (
            count(owner_sql, "SELECT count(*) FROM app.users WHERE id = :id", id=alice.user_id) == 0
        )
        # A nameless tombstone until its files are gone (the purges refer to it).
        ((name, deleted_at),) = owner_sql(
            "SELECT name, deleted_at FROM app.organizations WHERE id = :org", org=alice.org_id
        )
        assert (name, deleted_at is not None) == ("Deleted workspace", True)
        assert purges(owner_sql, alice.org_id)

        await maintenance.purge_deleted_files()
        for zone in ("bronze", "silver", "gold"):
            assert keys_under(raw_s3, f"{zone}/{alice.org_id}/") == [], zone
        assert purges(owner_sql, alice.org_id) == {}
        assert (
            count(
                owner_sql,
                "SELECT count(*) FROM app.organizations WHERE id = :org",
                org=alice.org_id,
            )
            == 0
        )

        # Another workspace is untouched.
        theirs = await client.get(f"/v1/datasets/{bobs}", headers=auth(bob))
        assert theirs.status_code == 200

    async def test_an_empty_workspace_leaves_nothing_behind(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, owner_sql: OwnerSql
    ) -> None:
        headers = auth(alice)
        assert (await client.get("/v1/me", headers=headers)).status_code == 200
        assert (await client.delete("/v1/me", headers=headers)).status_code == 200
        assert (
            count(
                owner_sql,
                "SELECT count(*) FROM app.organizations WHERE id = :org",
                org=alice.org_id,
            )
            == 0
        )

    async def test_refused_while_an_analysis_is_running(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, owner_sql: OwnerSql
    ) -> None:
        headers = auth(alice)
        dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
        run_id = (await start_run(client, headers, dataset_id)).json()["id"]
        owner_sql("UPDATE app.runs SET status = 'running' WHERE id = :id", id=run_id)

        refused = await client.delete("/v1/me", headers=headers)
        assert (refused.status_code, refused.json()["code"]) == (409, "still-running")
        assert rows_left(owner_sql, [run_id]).get("runs") == 1
        assert (await client.get(f"/v1/datasets/{dataset_id}", headers=headers)).status_code == 200
