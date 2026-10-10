"""``GET /v1/runs/{id}/events``: replay, Last-Event-ID, ticket audience separation, CORS, and a
live stream on a real server woken by PostgreSQL ``NOTIFY``."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
import uvicorn

from insightfulcell.api.app import create_app
from insightfulcell.api.container import Services
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import Phase
from insightfulcell.pipeline.definition import default_pipeline
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity, MakeTicket
from tests.integration.conftest import AuthHeaders
from tests.integration.runs_support import RecordingQueue, create_dataset, start_run

pytestmark = pytest.mark.integration

WEB_ORIGIN = "http://localhost:3000"


@dataclass
class Message:
    id: int | None
    event: str | None
    data: dict[str, Any] | None


def parse_stream(text: str) -> tuple[list[Message], list[str]]:
    """(messages, comment lines) of an SSE body."""
    messages, comments = [], []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                comments.append(line)
            elif ": " in line:
                name, value = line.split(": ", 1)
                fields[name] = value
        if "data" in fields:
            messages.append(
                Message(
                    id=int(fields["id"]) if "id" in fields else None,
                    event=fields.get("event"),
                    data=json.loads(fields["data"]),
                )
            )
    return messages, comments


@pytest.fixture
def executor(services: Services, settings: Settings) -> RunExecutor:
    return RunExecutor(
        TenantDatabase(services.engine),
        S3ObjectStore(settings),
        RecordingQueue(),
        default_pipeline(),
        settings,
    )


async def finished_run(
    client: httpx.AsyncClient, headers: dict[str, str], executor: RunExecutor, org_id: UUID
) -> str:
    dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
    run_id = (await start_run(client, headers, dataset_id)).json()["id"]
    assert await executor.execute(UUID(run_id), org_id, Phase.A) is Outcome.PHASE_COMPLETED
    assert await executor.execute(UUID(run_id), org_id, Phase.B) is Outcome.SUCCEEDED
    return str(run_id)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestReplay:
    async def test_a_finished_run_replays_every_event_then_ends(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        make_ticket: MakeTicket,
    ) -> None:
        run_id = await finished_run(client, auth(alice), executor, alice.org_id)
        response = await client.get(
            f"/v1/runs/{run_id}/events", headers=bearer(make_ticket(alice, run_id))
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.text.startswith("retry: 3000\n\n")
        messages, _ = parse_stream(response.text)
        assert [m.id for m in messages] == list(range(1, 14))
        assert [m.event for m in messages] == [
            "run.queued",
            "run.started",
            *["stage.started", "stage.completed"] * 4,  # ingest, profile, understand, clean_plan
            "phase.completed",
            "phase.completed",
            "run.succeeded",
        ]
        assert [m.data["stage"] for m in messages[3:10:2] if m.data] == [
            "ingest",
            "profile",
            "understand",
            "clean_plan",
        ]
        completed = messages[3].data
        assert completed is not None
        assert completed["stage"] == "ingest"
        assert completed["data"]["rows"] == 5
        assert completed["data"]["columns_count"] == 3
        assert "duration_ms" in completed["data"]
        assert {"seq", "type", "stage", "data", "at"} <= set(completed)

    async def test_last_event_id_resumes_after_that_event(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        executor: RunExecutor,
        make_ticket: MakeTicket,
    ) -> None:
        run_id = await finished_run(client, auth(alice), executor, alice.org_id)
        response = await client.get(
            f"/v1/runs/{run_id}/events",
            headers={**bearer(make_ticket(alice, run_id)), "Last-Event-ID": "11"},
        )
        messages, _ = parse_stream(response.text)
        assert [m.id for m in messages] == [12, 13]


class TestTickets:
    async def test_no_ticket_is_401(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        response = await client.get(f"/v1/runs/{run_id}/events")
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_an_api_token_is_not_a_ticket_and_a_ticket_is_not_an_api_token(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_ticket: MakeTicket,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        as_ticket = await client.get(f"/v1/runs/{run_id}/events", headers=auth(alice))
        assert as_ticket.status_code == 401
        as_token = await client.get(
            f"/v1/runs/{run_id}", headers=bearer(make_ticket(alice, run_id))
        )
        assert as_token.status_code == 401

    async def test_a_ticket_for_another_run_or_organisation_finds_nothing(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        make_ticket: MakeTicket,
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        first = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        second = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        wrong_run = await client.get(
            f"/v1/runs/{second}/events", headers=bearer(make_ticket(alice, first))
        )
        assert wrong_run.status_code == 404
        # Bob's ticket names Alice's run, but his organisation's RLS hides it.
        cross_tenant = await client.get(
            f"/v1/runs/{first}/events", headers=bearer(make_ticket(bob, first))
        )
        assert cross_tenant.status_code == 404
        assert cross_tenant.json()["code"] == "run-not-found"

    @pytest.mark.parametrize(
        "claims",
        [
            {"exp": int(time.time()) - 60},  # expired
            {"exp": int(time.time()) + 3_600},  # lives far longer than a ticket may
            {"run_id": "not-a-uuid"},
        ],
    )
    async def test_invalid_tickets_are_401(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        make_ticket: MakeTicket,
        claims: dict[str, Any],
    ) -> None:
        dataset_id = (await create_dataset(client, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(client, auth(alice), dataset_id)).json()["id"]
        response = await client.get(
            f"/v1/runs/{run_id}/events", headers=bearer(make_ticket(alice, run_id, **claims))
        )
        assert response.status_code == 401


class TestCors:
    async def test_preflight_from_the_web_app_is_allowed(self, client: httpx.AsyncClient) -> None:
        response = await client.options(
            "/v1/runs/01a120e9-cc81-7ec1-9428-29aea00bb693/events",
            headers={
                "Origin": WEB_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,last-event-id",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == WEB_ORIGIN
        assert "access-control-allow-credentials" not in response.headers

    async def test_other_origins_and_other_paths_get_no_cors(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        evil = await client.options(
            "/v1/runs/01a120e9-cc81-7ec1-9428-29aea00bb693/events",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in evil.headers
        api = await client.get("/v1/runs", headers={**auth(alice), "Origin": WEB_ORIGIN})
        assert api.status_code == 200
        assert "access-control-allow-origin" not in api.headers


# --------------------------------------------------------------------------- live


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def live_api(settings: Settings, services: Services) -> AsyncIterator[str]:
    """The API on a real socket, so the response really streams (ASGI test transports buffer)."""
    app = create_app(settings, services=services)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    await asyncio.wait_for(services.broker.connected.wait(), 10)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def test_a_live_stream_receives_events_as_the_worker_writes_them(
    live_api: str,
    auth: AuthHeaders,
    alice: Identity,
    executor: RunExecutor,
    make_ticket: MakeTicket,
) -> None:
    async with httpx.AsyncClient(base_url=live_api, timeout=30) as http:
        dataset_id = (await create_dataset(http, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(http, auth(alice), dataset_id)).json()["id"]

        received: list[str] = []
        first_event = asyncio.Event()

        async def listen() -> None:
            headers = {**bearer(make_ticket(alice, run_id)), "Origin": WEB_ORIGIN}
            async with http.stream("GET", f"/v1/runs/{run_id}/events", headers=headers) as resp:
                assert resp.status_code == 200
                assert resp.headers["access-control-allow-origin"] == WEB_ORIGIN
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        received.append(line.removeprefix("event: "))
                        first_event.set()

        listener = asyncio.create_task(listen())
        await asyncio.wait_for(first_event.wait(), 10)
        assert received == ["run.queued"]  # replayed; the stream now waits for NOTIFY

        assert await executor.execute(UUID(run_id), alice.org_id, Phase.A) is (
            Outcome.PHASE_COMPLETED
        )
        assert await executor.execute(UUID(run_id), alice.org_id, Phase.B) is Outcome.SUCCEEDED
        # The stream ends by itself once the run is finished.
        await asyncio.wait_for(listener, 10)
    assert received == [
        "run.queued",
        "run.started",
        *["stage.started", "stage.completed"] * 4,  # ingest, profile, understand, clean_plan
        "phase.completed",
        "phase.completed",
        "run.succeeded",
    ]


async def test_a_stream_sends_keep_alives_and_closes_when_the_ticket_expires(
    live_api: str,
    auth: AuthHeaders,
    alice: Identity,
    make_ticket: MakeTicket,
    services: Services,
) -> None:
    services.settings = services.settings.model_copy(update={"events_keepalive_seconds": 0.3})
    async with httpx.AsyncClient(base_url=live_api, timeout=30) as http:
        dataset_id = (await create_dataset(http, auth(alice)))["dataset"]["id"]
        run_id = (await start_run(http, auth(alice), dataset_id)).json()["id"]
        now = int(time.time())
        ticket = make_ticket(alice, run_id, iat=now, exp=now + 2)
        started = time.monotonic()
        response = await http.get(f"/v1/runs/{run_id}/events", headers=bearer(ticket))
    assert time.monotonic() - started < 10
    messages, comments = parse_stream(response.text)
    assert [m.event for m in messages] == ["run.queued"]
    assert ": keep-alive" in comments
