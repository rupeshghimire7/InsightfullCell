"""The worker's executor against real PostgreSQL and S3: the real ``ingest`` stage on real
uploads, and fake stages for the paths the state machine must handle (review gate, cancel at a
stage boundary, checkpoint reuse, retries, crashes, the large queue, per-organisation limits)."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest

from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import ParseFailure, StorageUnavailable
from insightfulcell.domain.runs import Phase, Queue
from insightfulcell.domain.security import Principal, Role
from insightfulcell.pipeline.definition import Pipeline, default_pipeline
from insightfulcell.pipeline.stage import Stage
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.services.runs import RunService
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.helpers import complete, csv_bytes, put_parts, start_upload
from tests.integration.runs_support import (
    FakeStage,
    RecordingQueue,
    approve,
    create_dataset,
    never_pause,
    start_run,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration

MakeExecutor = Callable[..., tuple[RunExecutor, RecordingQueue]]


@pytest.fixture
def make_executor(services: Services, settings: Settings) -> MakeExecutor:
    """An executor for a pipeline; runs submitted *afterwards* through the API are planned with
    the same pipeline (as in production, where the API and the worker share one build)."""

    def _make(
        *stages: Stage, phase_b: tuple[Stage, ...] = (), **overrides: Any
    ) -> tuple[RunExecutor, RecordingQueue]:
        queue = RecordingQueue()
        pipeline = Pipeline(phase_a=stages, phase_b=phase_b) if stages else default_pipeline()
        services.runs = RunService(services.db, services.queue, pipeline)
        executor = RunExecutor(
            TenantDatabase(services.engine),
            S3ObjectStore(settings),
            queue,
            pipeline,
            settings.model_copy(update=overrides),
        )
        return executor, queue

    return _make


async def submit(
    client: httpx.AsyncClient, headers: dict[str, str], data: bytes | None = None
) -> tuple[UUID, UUID]:
    dataset_id = (await create_dataset(client, headers, data=data))["dataset"]["id"]
    response = await start_run(client, headers, dataset_id)
    assert response.status_code == 202, response.text
    return UUID(response.json()["id"]), UUID(dataset_id)


def events(owner_sql: OwnerSql, run_id: UUID) -> list[tuple[str, str | None]]:
    rows = owner_sql(
        "SELECT type, stage FROM app.run_events WHERE run_id = :id ORDER BY seq", id=run_id
    )
    return [(str(t), s if s is None else str(s)) for t, s in rows]


def run_row(owner_sql: OwnerSql, run_id: UUID) -> dict[str, Any]:
    columns = (
        "status, phase, stage, queue, attempts, crash_count, failure_code, failure_detail, "
        "failure_hint, finished_at, review_requested_at"
    )
    (row,) = owner_sql(f"SELECT {columns} FROM app.runs WHERE id = :id", id=run_id)
    return dict(zip([c.strip() for c in columns.split(",")], row, strict=True))


def stage_rows(owner_sql: OwnerSql, run_id: UUID) -> dict[str, tuple[Any, ...]]:
    rows = owner_sql(
        "SELECT name, status, reused, attempts, output, artifacts FROM app.run_stages "
        "WHERE run_id = :id ORDER BY position",
        id=run_id,
    )
    return {str(r[0]): r[1:] for r in rows}


class TestRealIngest:
    async def test_a_csv_runs_end_to_end(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        await create_dataset(client, auth(alice))  # creates the organisation
        never_pause(owner_sql, alice.org_id)  # this test is about execution, not review
        run_id, _ = await submit(client, auth(alice), csv_bytes(40))
        executor, queue = make_executor()

        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED
        assert [(job.phase, job.queue, delay) for job, delay in queue.deferred] == [
            (Phase.B, Queue.STANDARD, 0)
        ]
        status, reused, attempts, output, artifacts = stage_rows(owner_sql, run_id)["ingest"]
        assert (status, reused, attempts) == ("succeeded", False, 1)
        assert output["rows"] == 40
        assert output["column_count"] == 3
        assert [c["name"] for c in output["columns"]] == ["id", "name", "amount"]
        assert output["format"] == "csv"
        key = artifacts["data"]
        assert key.startswith(f"gold/{alice.org_id}/{run_id}/ingest/")
        assert raw_s3.get_object(Bucket="ic-test", Key=key)["Body"].read(4) == b"PAR1"

        assert await executor.execute(run_id, alice.org_id, Phase.B) is Outcome.SUCCEEDED
        row = run_row(owner_sql, run_id)
        assert (row["status"], row["phase"], row["stage"]) == ("succeeded", "B", None)
        assert row["finished_at"] is not None
        phase_a = [
            (kind, stage)
            for stage in ("ingest", "profile", "understand", "clean_plan")
            for kind in ("stage.started", "stage.completed")
        ]
        phase_b = [
            (kind, stage)
            for stage in (
                "clean_apply",
                "geo",
                "features",
                "mine",
                "sensitivity",
                "rank",
                "visualize",
                "report",
            )
            for kind in ("stage.started", "stage.completed")
        ]
        assert events(owner_sql, run_id) == [
            ("run.queued", None),
            ("run.started", None),
            *phase_a,
            ("phase.completed", None),
            *phase_b,
            ("phase.completed", None),
            ("run.succeeded", None),
        ]
        # Phase B answered the plan's questions with stored facts and wrote the report.
        facts = owner_sql("SELECT count(*) > 0 FROM app.run_facts WHERE run_id = :id", id=run_id)
        assert facts == [(True,)]
        assert owner_sql(
            "SELECT revision, narration_source FROM app.run_reports WHERE run_id = :id", id=run_id
        ) == [(1, "engine")]
        # Phase A recorded the run's columns, a plan (rules: no model in tests) and a cleaning
        # plan approved by the organisation's policy (auto_safe: no review).
        columns = owner_sql(
            "SELECT name, meaning, personal_data FROM app.run_columns WHERE run_id = :id "
            "ORDER BY position",
            id=run_id,
        )
        assert columns == [
            ("id", "identifier", None),
            ("name", "identifier", None),  # unique codes; a bare "name" is a person only by value
            ("amount", "currency", None),
        ]
        plans = owner_sql(
            "SELECT kind, revision, source, fallback_reason FROM app.analysis_plans "
            "WHERE run_id = :id",
            id=run_id,
        )
        assert plans == [("resolved", 1, "rules", "disabled")]
        assert owner_sql(
            "SELECT revision, policy, status FROM app.cleaning_plans WHERE run_id = :id",
            id=run_id,
        ) == [(1, "auto_safe", "approved")]

    async def test_every_plan_is_reviewed_and_approval_continues_to_the_report(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, _ = await submit(client, auth(alice), csv_bytes(40))
        executor, queue = make_executor()
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.AWAITING_REVIEW
        assert queue.deferred == []  # nothing goes on until a person approves
        assert run_row(owner_sql, run_id)["status"] == "awaiting_review"
        response = await approve(client, auth(alice), str(run_id))
        assert response.status_code == 200, response.text
        assert await executor.execute(run_id, alice.org_id, Phase.B) is Outcome.SUCCEEDED
        report = (await client.get(f"/v1/runs/{run_id}/report", headers=auth(alice))).json()
        facts = {f["id"] for f in report["facts"]}
        answered = [s for s in report["sections"] if not s["skipped"]]
        assert answered
        assert all(
            set(sentence["citations"]) <= facts for s in answered for sentence in s["answer"]
        )

    async def test_bytes_that_do_not_match_the_recorded_checksum_fail_the_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        """ADR-026: the browser's digest is a claim until the worker re-hashes the bytes."""
        data = csv_bytes(5)
        started = await start_upload(client, auth(alice), filename="lie.csv", size_bytes=len(data))
        ticket = started.json()
        parts = await put_parts(ticket, data)
        wrong = hashlib.sha256(b"something else").hexdigest()
        done = await complete(client, auth(alice), ticket["upload_id"], parts, data, sha256=wrong)
        assert done.status_code == 200
        run = await start_run(client, auth(alice), done.json()["dataset"]["id"])
        run_id = UUID(run.json()["id"])

        executor, queue = make_executor()
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        row = run_row(owner_sql, run_id)
        assert row["status"] == "failed"
        assert row["failure_code"] == "integrity-mismatch"
        assert row["failure_hint"] == "Upload the file again."
        assert stage_rows(owner_sql, run_id)["ingest"][0] == "failed"
        assert events(owner_sql, run_id)[-2:] == [
            ("stage.failed", "ingest"),
            ("run.failed", "ingest"),
        ]
        assert queue.deferred == []  # data problems are not retried

    async def test_an_unparseable_file_fails_with_a_reason_and_no_cell_values(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        run_id, _ = await submit(client, auth(alice), b"secret_column\n")
        executor, _ = make_executor()
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        row = run_row(owner_sql, run_id)
        assert row["failure_code"] == "parse-failure"
        assert "no data rows" in row["failure_detail"]

    async def test_a_purged_original_fails_as_source_missing(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
        raw_s3: S3Client,
    ) -> None:
        run_id, _ = await submit(client, auth(alice))
        ((key,),) = owner_sql(
            "SELECT v.bronze_key FROM app.dataset_versions v "
            "JOIN app.runs r ON r.dataset_version_id = v.id WHERE r.id = :id",
            id=run_id,
        )
        raw_s3.delete_object(Bucket="ic-test", Key=str(key))
        executor, _ = make_executor()
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        assert run_row(owner_sql, run_id)["failure_code"] == "source-missing"


class TestStateMachine:
    async def test_unknown_run_and_wrong_organisation_are_not_found(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        executor, _ = make_executor(FakeStage("one"))
        run_id, _ = await submit(client, auth(alice))
        # A forged job naming Bob's organisation finds nothing under row-level security.
        assert await executor.execute(run_id, bob.org_id, Phase.A) is Outcome.NOT_FOUND
        assert run_row(owner_sql, run_id)["status"] == "queued"

    async def test_stale_jobs_are_skipped(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        stage = FakeStage("one")
        executor, _ = make_executor(stage)
        run_id, _ = await submit(client, auth(alice))
        assert await executor.execute(run_id, alice.org_id, Phase.B) is Outcome.SKIPPED
        await client.post(f"/v1/runs/{run_id}:cancel", headers=auth(alice))
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.SKIPPED
        assert stage.calls == 0

    async def test_review_gate_pauses_then_approval_runs_phase_b(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        services: Services,
        owner_sql: OwnerSql,
    ) -> None:
        gate_stage = FakeStage("plan", needs_review=True)
        after = FakeStage("apply")
        executor, queue = make_executor(gate_stage, phase_b=(after,))
        run_id, _ = await submit(client, auth(alice))

        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.AWAITING_REVIEW
        row = run_row(owner_sql, run_id)
        assert row["status"] == "awaiting_review"
        assert row["review_requested_at"] is not None
        assert queue.deferred == []

        principal = Principal(alice.user_id, alice.org_id, Role.OWNER)
        services.runs._queue = queue  # record instead of queueing
        detail = await services.runs.approve_review(principal, run_id)
        assert (detail.run.status, detail.run.phase) == ("queued", "B")
        assert [(j.phase, j.queue) for j, _ in queue.deferred] == [(Phase.B, Queue.STANDARD)]

        assert await executor.execute(run_id, alice.org_id, Phase.B) is Outcome.SUCCEEDED
        assert gate_stage.calls == 1  # Phase B reads Phase A's stored result
        assert after.calls == 1
        assert ("review.required", None) in events(owner_sql, run_id)
        assert ("review.approved", None) in events(owner_sql, run_id)

    async def test_cancel_takes_effect_at_the_next_stage_boundary(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        gate = asyncio.Event()
        first, second = FakeStage("one", gate=gate), FakeStage("two")
        executor, queue = make_executor(first, second)
        run_id, _ = await submit(client, auth(alice))

        task = asyncio.create_task(executor.execute(run_id, alice.org_id, Phase.A))
        await asyncio.wait_for(first.started.wait(), 10)
        cancel = await client.post(f"/v1/runs/{run_id}:cancel", headers=auth(alice))
        assert cancel.json()["status"] == "cancelled"
        gate.set()

        assert await asyncio.wait_for(task, 10) is Outcome.STOPPED
        assert second.calls == 0
        assert run_row(owner_sql, run_id)["status"] == "cancelled"
        # The first stage's work finished and keeps its checkpoint.
        assert stage_rows(owner_sql, run_id)["one"][0] == "succeeded"
        assert queue.deferred == []

    async def test_transient_failures_retry_with_checkpoints_then_fail(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        first = FakeStage("one")
        flaky = FakeStage("two", fail={n: RuntimeError("boom 42") for n in (1, 2, 3)})
        executor, queue = make_executor(first, flaky, run_retry_delay_seconds=5)
        run_id, _ = await submit(client, auth(alice))

        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        assert [delay for _, delay in queue.deferred] == [5, 10]  # grows with each attempt
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED

        assert first.calls == 1  # reused from its checkpoint on attempts 2 and 3
        assert flaky.calls == 3
        row = run_row(owner_sql, run_id)
        assert row["failure_code"] == "internal-error"
        assert "boom" not in (row["failure_detail"] or "")  # exception text never shown
        assert stage_rows(owner_sql, run_id)["one"][1] is True  # reused
        kinds = [t for t, _ in events(owner_sql, run_id)]
        assert kinds.count("stage.retrying") == 2
        assert kinds.count("stage.reused") == 2

    async def test_user_retry_resumes_from_the_checkpoint(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        first = FakeStage("one")
        second = FakeStage("two", fail={1: ParseFailure("bad")})
        executor, _ = make_executor(first, second)
        run_id, _ = await submit(client, auth(alice))
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        assert run_row(owner_sql, run_id)["failure_code"] == "parse-failure"

        retried = await client.post(f"/v1/runs/{run_id}:retry", headers=auth(alice))
        assert retried.status_code == 200, retried.text
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED
        assert (first.calls, second.calls) == (1, 2)

    async def test_upstream_errors_are_retried_not_fatal(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        stage = FakeStage("one", fail={1: StorageUnavailable("down")})
        executor, _ = make_executor(stage)
        run_id, _ = await submit(client, auth(alice))
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED

    async def test_repeated_out_of_memory_moves_to_the_large_queue_then_fails(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        stage = FakeStage("one", fail={n: MemoryError() for n in (1, 2, 3)})
        executor, queue = make_executor(stage)
        run_id, _ = await submit(client, auth(alice))

        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        assert run_row(owner_sql, run_id)["queue"] == "standard"
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        assert run_row(owner_sql, run_id)["queue"] == "large"
        assert [job.queue for job, _ in queue.deferred] == [Queue.STANDARD, Queue.LARGE]
        assert ("run.moved_to_large_queue", None) in events(owner_sql, run_id)

        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.RETRYING
        # A fourth claim exceeds run_max_attempts (3): fail with a clear reason.
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        row = run_row(owner_sql, run_id)
        assert row["failure_code"] == "worker-crashed"
        assert row["crash_count"] == 3

    async def test_organisation_concurrency_limit_makes_runs_wait(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        gate = asyncio.Event()
        stage = FakeStage("one", gate=gate)
        executor, queue = make_executor(stage, run_capacity_wait_seconds=7)
        first_id, _ = await submit(client, auth(alice))
        second_id, _ = await submit(client, auth(alice))
        owner_sql(
            "UPDATE app.organizations SET max_concurrent_runs = 1 WHERE id = :id", id=alice.org_id
        )

        running = asyncio.create_task(executor.execute(first_id, alice.org_id, Phase.A))
        await asyncio.wait_for(stage.started.wait(), 10)
        assert await executor.execute(second_id, alice.org_id, Phase.A) is Outcome.WAITING
        assert await executor.execute(second_id, alice.org_id, Phase.A) is Outcome.WAITING
        assert run_row(owner_sql, second_id)["status"] == "queued"
        waits = [(job.run_id, delay) for job, delay in queue.deferred]
        assert waits == [(second_id, 7), (second_id, 7)]
        assert [t for t, _ in events(owner_sql, second_id)].count("run.waiting") == 1

        gate.set()
        assert await asyncio.wait_for(running, 10) is Outcome.PHASE_COMPLETED
        # Between its phases a run still holds its slot; it frees it when it finishes.
        assert await executor.execute(second_id, alice.org_id, Phase.A) is Outcome.WAITING
        assert await executor.execute(first_id, alice.org_id, Phase.B) is Outcome.SUCCEEDED
        assert await executor.execute(second_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED

    async def test_phase_a_completes_even_if_deferring_phase_b_fails(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        """The reconciler re-defers it; the run is never lost."""
        executor, queue = make_executor(FakeStage("one"))
        run_id, _ = await submit(client, auth(alice))
        queue.fail_defer = True
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED
        row = run_row(owner_sql, run_id)
        assert (row["status"], row["phase"]) == ("running", "B")

    async def test_a_changed_pipeline_cannot_continue_an_old_run(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        old, _ = make_executor(FakeStage("one"))
        run_id, _ = await submit(client, auth(alice))
        assert await old.execute(run_id, alice.org_id, Phase.A) is Outcome.PHASE_COMPLETED
        new, _ = make_executor(FakeStage("renamed"))
        assert await new.execute(run_id, alice.org_id, Phase.B) is Outcome.FAILED
        assert run_row(owner_sql, run_id)["failure_code"] == "pipeline-changed"

    async def test_a_run_planned_by_another_pipeline_fails_before_any_work(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_executor: MakeExecutor,
        owner_sql: OwnerSql,
    ) -> None:
        make_executor(FakeStage("one"))
        run_id, _ = await submit(client, auth(alice))
        stage = FakeStage("two")
        executor, _ = make_executor(stage)
        assert await executor.execute(run_id, alice.org_id, Phase.A) is Outcome.FAILED
        assert run_row(owner_sql, run_id)["failure_code"] == "pipeline-changed"
        assert stage.calls == 0
