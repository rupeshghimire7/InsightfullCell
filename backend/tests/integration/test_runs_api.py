"""``/v1/runs``: submit (202, idempotent, queued job), read, list, cancel, retry, If-Match, roles
and tenant isolation, against real PostgreSQL (RLS) and the real Procrastinate queue."""

from __future__ import annotations

import httpx
import pytest

from insightfulcell.queue.procrastinate_queue import ProcrastinateQueue
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.runs_support import create_dataset, start_run

pytestmark = pytest.mark.integration

UNKNOWN = "00000000-0000-7000-8000-000000000000"


class TestSubmit:
    async def test_accepts_with_202_queued_run_and_a_job(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        uploaded = await create_dataset(client, auth(alice))
        response = await start_run(client, auth(alice), uploaded["dataset"]["id"])

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["phase"] == "A"
        assert body["queue"] == "standard"
        assert body["dataset_version_id"] == uploaded["version"]["id"]
        assert body["dataset_name"] == uploaded["dataset"]["name"]
        assert body["events_url"] == f"/v1/runs/{body['id']}/events"
        assert response.headers["location"] == f"/v1/runs/{body['id']}"
        assert response.headers["etag"] == f'"{body["row_version"]}"'
        assert [(s["name"], s["status"], s["phase"]) for s in body["stages"]] == [
            ("ingest", "pending", "A"),
            ("profile", "pending", "A"),
            ("understand", "pending", "A"),
            ("clean_plan", "pending", "A"),
            ("clean_apply", "pending", "B"),
            ("geo", "pending", "B"),
            ("features", "pending", "B"),
            ("mine", "pending", "B"),
            ("sensitivity", "pending", "B"),
            ("rank", "pending", "B"),
            ("visualize", "pending", "B"),
            ("report", "pending", "B"),
        ]
        assert body["last_event_seq"] == 1
        assert body["failure"] is None

        jobs = owner_sql(
            "SELECT task_name, queue_name, lock, queueing_lock, status, args "
            "FROM procrastinate.procrastinate_jobs"
        )
        assert len(jobs) == 1
        task, queue, lock, queueing_lock, status, args = jobs[0]
        assert (task, queue, status) == ("insightfulcell.run_phase", "standard", "todo")
        assert lock == queueing_lock == body["id"]
        assert args == {"run_id": body["id"], "org_id": str(alice.org_id), "phase": "A"}

        events = owner_sql("SELECT seq, type FROM app.run_events WHERE run_id = :id", id=body["id"])
        assert events == [(1, "run.queued")]

    async def test_same_idempotency_key_returns_the_same_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        first = await start_run(client, auth(alice), dataset_id, key="same-key-1234")
        second = await start_run(client, auth(alice), dataset_id, key="same-key-1234")
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert owner_sql("SELECT count(*) FROM app.runs") == [(1,)]
        assert owner_sql("SELECT count(*) FROM procrastinate.procrastinate_jobs") == [(1,)]

    async def test_reusing_a_key_for_another_request_is_refused(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        one = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        two = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        assert (await start_run(client, auth(alice), one, key="key-reused-1")).status_code == 202
        response = await start_run(client, auth(alice), two, key="key-reused-1")
        assert response.status_code == 422
        assert response.json()["code"] == "idempotency-key-reused"

    async def test_without_idempotency_key_is_rejected(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        response = await client.post(
            "/v1/runs", json={"dataset_id": dataset_id}, headers=auth(alice)
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid-request"

    async def test_explicit_version_is_used(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        first = await create_dataset(client, auth(alice))
        dataset_id = first["dataset"]["id"]
        response = await start_run(
            client, auth(alice), dataset_id, version_id=first["version"]["id"]
        )
        assert response.status_code == 202
        assert response.json()["dataset_version_id"] == first["version"]["id"]

    @pytest.mark.parametrize(
        ("dataset", "version", "code"),
        [
            (UNKNOWN, None, "dataset-not-found"),
            ("own", UNKNOWN, "dataset-version-not-found"),
        ],
    )
    async def test_unknown_dataset_or_version_is_404(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        dataset: str,
        version: str | None,
        code: str,
    ) -> None:
        own = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        response = await start_run(
            client, auth(alice), own if dataset == "own" else dataset, version_id=version
        )
        assert response.status_code == 404
        assert response.json()["code"] == code

    async def test_a_version_of_another_dataset_is_not_found(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        one = await create_dataset(client, auth(alice))
        two = await create_dataset(client, auth(alice))
        response = await start_run(
            client, auth(alice), one["dataset"]["id"], version_id=two["version"]["id"]
        )
        assert response.status_code == 404
        assert response.json()["code"] == "dataset-version-not-found"

    async def test_purged_version_cannot_be_analysed(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        uploaded = await create_dataset(client, auth(alice))
        owner_sql(
            "UPDATE app.dataset_versions SET purged_at = now() WHERE id = :id",
            id=uploaded["version"]["id"],
        )
        response = await start_run(client, auth(alice), uploaded["dataset"]["id"])
        assert response.status_code == 409
        assert response.json()["code"] == "dataset-version-purged"

    async def test_viewer_cannot_start_a_run_but_can_read_it(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        viewer = Identity(user_id=alice.user_id, org_id=alice.org_id, role="viewer")

        refused = await start_run(client, auth(viewer), dataset_id)
        assert refused.status_code == 403
        assert refused.json()["code"] == "forbidden"
        assert (await client.get(f"/v1/runs/{run_id}", headers=auth(viewer))).status_code == 200
        cancel = await client.post(f"/v1/runs/{run_id}:cancel", headers=auth(viewer))
        assert cancel.status_code == 403

    async def test_queue_outage_still_commits_the_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The run is the source of truth: a failed defer leaves it queued for the reconciler."""
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]

        async def broken(self: object, job: object, *, delay_seconds: int = 0) -> None:
            raise ConnectionError("queue down")

        monkeypatch.setattr(ProcrastinateQueue, "defer_phase", broken)
        response = await start_run(client, auth(alice), dataset_id)
        assert response.status_code == 202
        assert owner_sql("SELECT status FROM app.runs") == [("queued",)]
        assert owner_sql("SELECT count(*) FROM procrastinate.procrastinate_jobs") == [(0,)]


class TestRead:
    async def test_get_returns_stages_and_etag(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        created = (await start_run(client, auth(alice), dataset_id)).json()
        response = await client.get(f"/v1/runs/{created['id']}", headers=auth(alice))
        assert response.status_code == 200
        # No worker runs in these tests: a read of a queued run says so.
        assert response.json() == {**created, "waiting_for_worker": True}
        assert response.headers["etag"] == f'"{created["row_version"]}"'

    async def test_list_is_keyset_paginated_and_filterable(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        one = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        two = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        made = [(await start_run(client, auth(alice), d)).json()["id"] for d in (one, two, one)]

        seen: list[str] = []
        cursor: str | None = None
        while True:
            params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            body = (await client.get("/v1/runs", params=params, headers=auth(alice))).json()
            seen += [item["id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert seen == list(reversed(made))

        filtered = await client.get("/v1/runs", params={"dataset_id": one}, headers=auth(alice))
        assert [item["id"] for item in filtered.json()["items"]] == [made[2], made[0]]

    async def test_invalid_cursor_is_422(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await client.get("/v1/runs", params={"cursor": "!!"}, headers=auth(alice))
        assert response.status_code == 422
        assert response.json()["code"] == "invalid-cursor"

    async def test_dataset_list_shows_the_latest_run(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        before = (await client.get("/v1/datasets", headers=auth(alice))).json()["items"][0]
        assert before["latest_run"] is None
        await start_run(client, auth(alice), dataset_id)
        newest = (await start_run(client, auth(alice), dataset_id)).json()
        item = (await client.get("/v1/datasets", headers=auth(alice))).json()["items"][0]
        assert item["latest_run"]["id"] == newest["id"]
        assert item["latest_run"]["status"] == "queued"
        detail = (await client.get(f"/v1/datasets/{dataset_id}", headers=auth(alice))).json()
        assert detail["dataset"]["latest_run"]["id"] == newest["id"]


class TestCancel:
    async def test_cancel_a_queued_run_and_repeat_safely(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run = (await start_run(client, auth(alice), dataset_id)).json()

        response = await client.post(
            f"/v1/runs/{run['id']}:cancel",
            headers={**auth(alice), "If-Match": f'"{run["row_version"]}"'},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "cancelled"
        assert body["finished_at"] is not None
        assert body["row_version"] == run["row_version"] + 1

        again = await client.post(f"/v1/runs/{run['id']}:cancel", headers=auth(alice))
        assert again.status_code == 200
        assert again.json()["row_version"] == body["row_version"]
        types = owner_sql(
            "SELECT type FROM app.run_events WHERE run_id = :id ORDER BY seq", id=run["id"]
        )
        assert types == [("run.queued",), ("run.cancelled",)]

    @pytest.mark.parametrize("if_match", ['"999"', 'W/"999"', "not-a-version"])
    async def test_stale_or_garbage_if_match_is_412(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        if_match: str,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run = (await start_run(client, auth(alice), dataset_id)).json()
        response = await client.post(
            f"/v1/runs/{run['id']}:cancel", headers={**auth(alice), "If-Match": if_match}
        )
        assert response.status_code == 412
        assert response.json()["code"] == "run-changed"
        still = (await client.get(f"/v1/runs/{run['id']}", headers=auth(alice))).json()
        assert still["status"] == "queued"

    async def test_finished_runs_cannot_be_cancelled(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        owner_sql(
            "UPDATE app.runs SET status = 'succeeded', finished_at = now() WHERE id = :id",
            id=run_id,
        )
        response = await client.post(f"/v1/runs/{run_id}:cancel", headers=auth(alice))
        assert response.status_code == 409
        assert response.json()["code"] == "run-state-conflict"


class TestRetry:
    async def test_only_failed_runs_can_be_retried(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        response = await client.post(f"/v1/runs/{run_id}:retry", headers=auth(alice))
        assert response.status_code == 409
        assert response.json()["code"] == "run-state-conflict"

    async def test_retry_requeues_a_failed_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        owner_sql: OwnerSql,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        owner_sql("DELETE FROM procrastinate.procrastinate_jobs")
        owner_sql(
            "UPDATE app.runs SET status = 'failed', finished_at = now(), attempts = 3, "
            "failure_code = 'parse-failure', failure_detail = 'x' WHERE id = :id",
            id=run_id,
        )
        owner_sql("UPDATE app.run_stages SET status = 'failed' WHERE run_id = :id", id=run_id)

        response = await client.post(f"/v1/runs/{run_id}:retry", headers=auth(alice))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["failure"] is None
        assert body["finished_at"] is None
        assert body["attempts"] == 0
        assert {s["status"] for s in body["stages"]} == {"pending"}
        jobs = owner_sql("SELECT args->>'phase', status FROM procrastinate.procrastinate_jobs")
        assert jobs == [("A", "todo")]


class TestIsolation:
    async def test_another_organisation_cannot_see_or_change_a_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]

        for method, path in [
            ("GET", f"/v1/runs/{run_id}"),
            ("POST", f"/v1/runs/{run_id}:cancel"),
            ("POST", f"/v1/runs/{run_id}:retry"),
        ]:
            response = await client.request(method, path, headers=auth(bob))
            assert response.status_code == 404, (method, path)
            assert response.json()["code"] == "run-not-found"
        assert (await client.get("/v1/runs", headers=auth(bob))).json()["items"] == []
        # Bob cannot start a run on Alice's dataset either.
        refused = await start_run(client, auth(bob), dataset_id)
        assert refused.status_code == 404
