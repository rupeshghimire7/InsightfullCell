"""Test doubles and helpers for runs: a queue that records instead of queueing, stages whose
behaviour a test controls, and the API calls that create a dataset and start a run."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from insightfulcell.domain.runs import Queue
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult
from insightfulcell.queue.port import PhaseJob, StalledJob
from tests.integration.helpers import csv_bytes, upload_file


@dataclass
class RecordingQueue:
    """Implements ``JobQueue`` by remembering what was asked of it."""

    deferred: list[tuple[PhaseJob, int]] = field(default_factory=list)
    stalled: list[StalledJob] = field(default_factory=list)
    retried: list[tuple[int, Queue]] = field(default_factory=list)
    abandoned: list[int] = field(default_factory=list)
    discarded: list[UUID] = field(default_factory=list)
    alive: bool | None = True
    fail_defer: bool = False

    async def defer_phase(self, job: PhaseJob, *, delay_seconds: int = 0) -> None:
        if self.fail_defer:
            raise ConnectionError("queue unreachable")
        self.deferred.append((job, delay_seconds))

    async def stalled_phase_jobs(self, seconds_since_heartbeat: float) -> Sequence[StalledJob]:
        return list(self.stalled)

    async def retry_stalled(self, job_id: int, queue: Queue) -> None:
        self.retried.append((job_id, queue))

    async def abandon_stalled(self, job_id: int) -> None:
        self.abandoned.append(job_id)

    async def discard_phase_jobs(self, run_ids: Sequence[UUID]) -> int:
        self.discarded.extend(run_ids)
        return len(run_ids)

    async def workers_alive(self, within_seconds: float) -> bool | None:
        return self.alive


Behaviour = Callable[[StageContext, int], Any]


class FakeStage:
    """A stage whose result a test decides. ``fail`` maps a call number (1-based) to the
    exception to raise on that call; ``gate`` holds the stage until the test releases it."""

    version = "1"

    def __init__(
        self,
        name: str,
        *,
        needs_review: bool = False,
        fail: Mapping[int, BaseException] | None = None,
        gate: asyncio.Event | None = None,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.needs_review = needs_review
        self.fail = dict(fail or {})
        self.gate = gate
        self.started = asyncio.Event()
        self.calls = 0
        self.output = dict(output or {"rows": 3})

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"sha256": source.sha256}

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls += 1
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.calls in self.fail:
            raise self.fail[self.calls]
        return StageResult(output=self.output, needs_review=self.needs_review)


async def create_dataset(
    client: httpx.AsyncClient, headers: dict[str, str], *, data: bytes | None = None
) -> dict[str, Any]:
    """Upload a small CSV; returns the ``:complete`` body (``dataset``, ``version``)."""
    tag = uuid.uuid4().hex[:8]
    response = await upload_file(
        client, headers, filename=f"sales-{tag}.csv", data=data or csv_bytes(5, tag=tag)
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def start_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    dataset_id: str,
    *,
    key: str | None = None,
    version_id: str | None = None,
    review_mode: str | None = "manual",
) -> httpx.Response:
    """Start a run; ``manual`` unless asked (these tests are about the review gate), ``None``
    sends no mode (the API's default, automatic)."""
    payload: dict[str, Any] = {"dataset_id": dataset_id}
    if review_mode is not None:
        payload["review_mode"] = review_mode
    if version_id is not None:
        payload["dataset_version_id"] = version_id
    return await client.post(
        "/v1/runs",
        json=payload,
        headers={**headers, "Idempotency-Key": key or f"run-{uuid.uuid4().hex}"},
    )


def run_ids(response: httpx.Response) -> tuple[UUID, UUID]:
    """(run id, dataset id) from a run response."""
    body = response.json()
    return UUID(body["id"]), UUID(body["dataset_id"])


async def approve(
    client: httpx.AsyncClient, headers: dict[str, str], run_id: str
) -> httpx.Response:
    """Approve a run waiting for review the way the review page does: every change still
    waiting for a decision is applied."""
    plan = (await client.get(f"/v1/runs/{run_id}/plan", headers=headers)).json()
    cleaning = plan["cleaning"]
    return await client.post(
        f"/v1/runs/{run_id}/cleaning-plan:decide",
        headers={**headers, "If-Match": f'"{plan["run"]["row_version"]}"'},
        json={
            "cleaning_revision": cleaning["revision"],
            "decisions": [
                {"operation_id": op["id"], "accept": True}
                for op in cleaning["operations"]
                if op["decision"] == "pending"
            ],
        },
    )


def never_pause(owner_sql: Callable[..., Any], org_id: UUID) -> None:
    """The organisation applies safe changes automatically and never pauses for review."""
    owner_sql(
        "UPDATE app.organizations SET cleaning_policy = 'auto_safe' WHERE id = :id", id=org_id
    )
