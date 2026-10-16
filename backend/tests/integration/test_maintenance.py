"""Periodic upkeep against real PostgreSQL and the real Procrastinate queue."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.queue.procrastinate_queue import ProcrastinateQueue, create_procrastinate_app
from insightfulcell.services.maintenance import Maintenance
from insightfulcell.services.run_executor import RunExecutor
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.helpers import start_upload
from tests.integration.runs_support import create_dataset, start_run

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration


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


async def submitted_run(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
    response = await start_run(client, headers, dataset_id)
    assert response.status_code == 202
    return str(response.json()["id"])


def jobs(owner_sql: OwnerSql) -> list[tuple[object, ...]]:
    return owner_sql(
        "SELECT lock, status, queue_name, args->>'phase' FROM procrastinate.procrastinate_jobs "
        "ORDER BY id"
    )


class TestReconciler:
    async def test_redefers_an_idle_active_run_without_a_job(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await submitted_run(client, auth(alice))
        owner_sql("DELETE FROM procrastinate.procrastinate_jobs")  # the defer was lost

        assert await maintenance.reconcile_runs() == 0  # not idle long enough yet
        owner_sql(
            "UPDATE app.runs SET updated_at = now() - interval '5 minutes' WHERE id = :id",
            id=run_id,
        )
        assert await maintenance.reconcile_runs() == 1
        assert jobs(owner_sql) == [(run_id, "todo", "standard", "A")]
        # A live job now exists: nothing more to do.
        assert await maintenance.reconcile_runs() == 0

    async def test_leaves_finished_and_waiting_runs_alone(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await submitted_run(client, auth(alice))
        owner_sql("DELETE FROM procrastinate.procrastinate_jobs")
        owner_sql(
            "UPDATE app.runs SET status = 'awaiting_review', review_requested_at = now(), "
            "updated_at = now() - interval '1 hour' WHERE id = :id",
            id=run_id,
        )
        assert await maintenance.reconcile_runs() == 0


class TestReaper:
    async def test_a_stalled_job_is_retried_and_moves_to_large_after_two_crashes(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await submitted_run(client, auth(alice))
        # A worker claimed the job, started the run, then died (no worker row, no heartbeat).
        owner_sql("UPDATE procrastinate.procrastinate_jobs SET status = 'doing'")
        owner_sql(
            "UPDATE app.runs SET status = 'running', stage = 'ingest', attempts = 1 WHERE id = :id",
            id=run_id,
        )
        owner_sql(
            "UPDATE app.run_stages SET status = 'running' WHERE run_id = :id AND name = 'ingest'",
            id=run_id,
        )

        assert await maintenance.reap_stalled_jobs() == 1
        assert jobs(owner_sql) == [(run_id, "todo", "standard", "A")]
        assert owner_sql("SELECT crash_count, queue FROM app.runs") == [(1, "standard")]
        assert owner_sql("SELECT DISTINCT status FROM app.run_stages") == [("pending",)]

        owner_sql("UPDATE procrastinate.procrastinate_jobs SET status = 'doing'")
        assert await maintenance.reap_stalled_jobs() == 1
        assert jobs(owner_sql) == [(run_id, "todo", "large", "A")]
        assert owner_sql("SELECT crash_count, queue FROM app.runs") == [(2, "large")]
        types = owner_sql(
            "SELECT type FROM app.run_events WHERE run_id = :id ORDER BY seq", id=run_id
        )
        assert ("run.moved_to_large_queue",) in types

    async def test_a_stalled_job_of_a_cancelled_run_is_abandoned(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        run_id = await submitted_run(client, auth(alice))
        await client.post(f"/v1/runs/{run_id}:cancel", headers=auth(alice))
        owner_sql("UPDATE procrastinate.procrastinate_jobs SET status = 'doing'")
        assert await maintenance.reap_stalled_jobs() == 1
        assert [row[1] for row in jobs(owner_sql)] == ["failed"]


class TestReviews:
    async def test_reminder_after_a_day_and_expiry_after_seven(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        reminded = await submitted_run(client, auth(alice))
        expired = await submitted_run(client, auth(alice))
        fresh = await submitted_run(client, auth(alice))
        for run_id, age in ((reminded, "2 days"), (expired, "8 days"), (fresh, "1 hour")):
            owner_sql(
                "UPDATE app.runs SET status = 'awaiting_review', "
                f"review_requested_at = now() - interval '{age}' WHERE id = :id",
                id=run_id,
            )

        sweep = await maintenance.sweep_reviews()
        assert (sweep.reminded, sweep.expired) == (1, 1)
        statuses = {str(i): str(st) for i, st in owner_sql("SELECT id::text, status FROM app.runs")}
        assert statuses == {
            reminded: "awaiting_review",
            expired: "expired",
            fresh: "awaiting_review",
        }
        again = await maintenance.sweep_reviews()
        assert (again.reminded, again.expired) == (0, 0)  # one reminder only
        detail = (await client.get(f"/v1/runs/{expired}", headers=auth(alice))).json()
        assert detail["finished_at"] is not None


class TestEventLog:
    async def test_partitions_are_created_ahead_and_rows_move_out_of_default(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        assert await maintenance.ensure_event_partitions() == 0  # the migration made them
        run_id = await submitted_run(client, auth(alice))
        # An event dated five months ahead has no partition yet: it lands in the default one.
        owner_sql(
            "INSERT INTO app.run_events (org_id, run_id, seq, type, created_at) "
            "VALUES (:org, :run, 99, 'run.started', "
            "date_trunc('month', now()) + interval '5 months' + interval '3 days')",
            org=alice.org_id,
            run=run_id,
        )
        assert owner_sql("SELECT count(*) FROM app.run_events_default") == [(1,)]
        assert owner_sql("SELECT app.ensure_run_event_partitions(5)") == [(3,)]
        assert owner_sql("SELECT count(*) FROM app.run_events_default") == [(0,)]
        assert owner_sql("SELECT count(*) FROM app.run_events WHERE seq = 99") == [(1,)]

    @pytest.mark.parametrize(
        "statement",
        ["UPDATE app.run_events SET type = 'run.failed'", "DELETE FROM app.run_events"],
    )
    async def test_events_are_append_only_even_for_the_owner(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
        statement: str,
    ) -> None:
        await submitted_run(client, auth(alice))
        with pytest.raises(DBAPIError, match="append-only"):
            owner_sql(statement)


class TestHousekeeping:
    async def test_abandoned_uploads_expire_and_their_parts_are_aborted(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        started = await start_upload(client, auth(alice), filename="never.csv", size_bytes=100)
        upload_id = started.json()["upload_id"]
        key = f"bronze/{alice.org_id}/uploads/{upload_id}"

        def open_parts() -> list[object]:
            listing = raw_s3.list_multipart_uploads(Bucket="ic-test", Prefix=key)
            return list(listing.get("Uploads", []))

        assert open_parts()
        assert await maintenance.expire_abandoned_uploads() == 0
        owner_sql(
            "UPDATE app.uploads SET created_at = now() - interval '2 days' WHERE id = :id",
            id=upload_id,
        )
        assert await maintenance.expire_abandoned_uploads() == 1
        assert owner_sql("SELECT status FROM app.uploads") == [("expired",)]
        assert open_parts() == []

    async def test_expired_idempotency_keys_are_purged(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        maintenance: Maintenance,
        owner_sql: OwnerSql,
    ) -> None:
        await submitted_run(client, auth(alice))  # one upload key and one run key
        assert await maintenance.purge_idempotency_keys() == 0
        owner_sql("UPDATE app.idempotency_keys SET created_at = now() - interval '2 days'")
        assert await maintenance.purge_idempotency_keys() == 2
        assert owner_sql("SELECT count(*) FROM app.idempotency_keys") == [(0,)]


async def test_maintenance_functions_return_ids_only_and_are_not_public(
    owner_sql: OwnerSql,
) -> None:
    """Definer functions run with the owner's rights, so PUBLIC must not execute them."""
    rows = owner_sql(
        "SELECT p.proname, has_function_privilege('public', p.oid, 'EXECUTE'), "
        "has_function_privilege('ic_app', p.oid, 'EXECUTE') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'app' AND p.prosecdef ORDER BY p.proname"
    )
    assert [(name, public, app) for name, public, app in rows] == [
        ("abandoned_uploads", False, True),
        ("delete_dataset", False, True),
        ("delete_run", False, True),
        ("ensure_llm_call_partitions", False, True),
        ("ensure_run_event_partitions", False, True),
        ("ensure_test_log_partitions", False, True),
        ("ensure_verification_partitions", False, True),
        # Internal: only the delete functions (running as the owner) may call it.
        ("erase_runs", False, False),
        ("purge_idempotency_keys", False, True),
        ("reviews_due", False, True),
        ("runs_without_live_job", False, True),
        ("storage_purges_due", False, True),
    ]
