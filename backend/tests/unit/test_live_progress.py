"""Event tickets, the LISTEN broker's fan-out, SSE framing and If-Match parsing."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from insightfulcell.api.cors import EVENTS_PATH
from insightfulcell.api.routers.runs import format_event, parse_if_match
from insightfulcell.auth.tokens import TokenVerifier
from insightfulcell.config import Settings
from insightfulcell.db.notifications import EventBroker
from insightfulcell.domain.errors import RunChanged, Unauthenticated
from insightfulcell.domain.security import Role
from insightfulcell.services.dto import RunEventInfo
from tests.conftest import EVENTS_AUDIENCE, Identity, MakeTicket, MakeToken


class TestTickets:
    def test_a_ticket_names_one_run_and_the_caller(
        self, ticket_verifier: TokenVerifier, make_ticket: MakeTicket
    ) -> None:
        who = Identity(user_id=uuid4(), org_id=uuid4(), role="analyst")
        run_id = uuid4()
        ticket = ticket_verifier.verify_ticket_sync(make_ticket(who, run_id))
        assert ticket.run_id == run_id
        assert ticket.principal.org_id == who.org_id
        assert ticket.principal.role is Role.ANALYST
        assert 0 < (ticket.expires_at - datetime.now(UTC)).total_seconds() <= 300

    def test_audiences_never_cross(
        self,
        verifier: TokenVerifier,
        ticket_verifier: TokenVerifier,
        make_token: MakeToken,
        make_ticket: MakeTicket,
    ) -> None:
        who = Identity(user_id=uuid4(), org_id=uuid4())
        with pytest.raises(Unauthenticated):
            verifier.verify_sync(make_ticket(who, uuid4()))
        with pytest.raises(Unauthenticated):
            ticket_verifier.verify_ticket_sync(make_token(who, run_id=str(uuid4())))

    @pytest.mark.parametrize(
        "override",
        [
            {"drop": ("run_id",)},
            {"run_id": "nope"},
            {"exp": int(time.time()) + 3600},
        ],
    )
    def test_malformed_tickets_are_refused(
        self, ticket_verifier: TokenVerifier, make_token: MakeToken, override: dict[str, object]
    ) -> None:
        claims: dict[str, object] = {"aud": EVENTS_AUDIENCE, "run_id": str(uuid4())}
        now = int(time.time())
        claims.update(iat=now, exp=now + 300)
        drop = override.pop("drop", ())
        claims.update(override)
        token = make_token(Identity(uuid4(), uuid4()), drop=drop, **claims)
        with pytest.raises(Unauthenticated):
            ticket_verifier.verify_ticket_sync(token)


class TestBroker:
    async def test_notifications_wake_only_that_runs_subscribers(self) -> None:
        broker = EventBroker(None)
        mine, other = uuid4(), uuid4()
        with broker.subscribe(mine) as a, broker.subscribe(mine) as b, broker.subscribe(other) as c:
            broker.dispatch(json.dumps({"run_id": str(mine), "seq": 3}))
            assert await a.wait(0.1)
            assert await b.wait(0.1)
            assert not await c.wait(0.05)
            assert not await a.wait(0.05)  # woken once per notification
        broker.dispatch(json.dumps({"run_id": str(mine), "seq": 4}))  # nobody left: no error

    @pytest.mark.parametrize("payload", ["not json", "{}", '{"run_id": "x"}', "[1]"])
    async def test_malformed_payloads_are_ignored(self, payload: str) -> None:
        broker = EventBroker(None)
        with broker.subscribe(uuid4()) as sub:
            broker.dispatch(payload)
            assert not await sub.wait(0.05)

    async def test_disabled_broker_starts_and_stops_without_a_database(self) -> None:
        broker = EventBroker(None)
        await broker.start()
        await broker.stop()
        assert not broker.connected.is_set()

    async def test_unreachable_database_keeps_retrying_quietly(self) -> None:
        broker = EventBroker("postgresql://nobody@127.0.0.1:1/none", connect_timeout=1)
        await broker.start()
        await asyncio.sleep(0.2)
        assert not broker.connected.is_set()
        await broker.stop()


def test_sse_framing() -> None:
    event = RunEventInfo(
        seq=4,
        type="stage.completed",
        stage="ingest",
        data={"rows": 10},
        created_at=datetime(2026, 10, 10, 1, 2, 3, tzinfo=UTC),
    )
    frame = format_event(event)
    assert frame.endswith("\n\n")
    lines = frame.strip().split("\n")
    assert lines[:2] == ["id: 4", "event: stage.completed"]
    assert json.loads(lines[2].removeprefix("data: ")) == {
        "seq": 4,
        "type": "stage.completed",
        "stage": "ingest",
        "data": {"rows": 10},
        "at": "2026-10-10T01:02:03+00:00",
    }


@pytest.mark.parametrize(("value", "expected"), [(None, None), ('"7"', 7), ('W/"7"', 7), ("7", 7)])
def test_if_match_accepts_etag_forms(value: str | None, expected: int | None) -> None:
    assert parse_if_match(value) == expected


@pytest.mark.parametrize("value", ["*", '"x"', "", "-1"])
def test_if_match_garbage_can_never_match(value: str) -> None:
    with pytest.raises(RunChanged):
        parse_if_match(value)


def test_cors_applies_to_the_event_stream_path_only() -> None:
    run = str(uuid4())
    assert EVENTS_PATH.fullmatch(f"/v1/runs/{run}/events")
    assert not EVENTS_PATH.fullmatch(f"/v1/runs/{run}")
    assert not EVENTS_PATH.fullmatch(f"/v1/runs/{run}/events/x")
    assert not EVENTS_PATH.fullmatch("/v1/datasets")


class TestSettings:
    def _settings(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "database_url": "postgresql+psycopg://u:p@h/db",
            "auth_jwks_url": "http://localhost/jwks",
            "auth_issuer": "http://localhost",
            "_env_file": None,
        }
        return Settings(**{**base, **overrides})  # type: ignore[arg-type]

    def test_origins_are_split_and_normalised(self) -> None:
        settings = self._settings(events_allowed_origins=" http://a.test/ , https://b.test,")
        assert settings.events_origins == ["http://a.test", "https://b.test"]
        assert settings.psycopg_conninfo == "postgresql://u:p@h/db"

    def test_ticket_audience_must_differ_from_the_api_audience(self) -> None:
        with pytest.raises(ValueError, match="AUTH_EVENTS_AUDIENCE"):
            self._settings(auth_events_audience="insightfulcell-api")

    def test_production_requires_https_origins(self) -> None:
        with pytest.raises(ValueError, match="EVENTS_ALLOWED_ORIGINS"):
            self._settings(
                environment="production",
                auth_jwks_url="https://auth.test/jwks",
                auth_issuer="https://auth.test",
                events_allowed_origins="http://insecure.test",
            )
