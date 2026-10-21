"""The spend cap and ledger, lifetime usage per person, and the admin overview, against a real
database as the application role."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
import sqlalchemy.exc
from pydantic import SecretStr
from sqlalchemy import text

from insightfulcell.api.app import create_app
from insightfulcell.api.container import Services, build_services
from insightfulcell.config import Settings
from insightfulcell.llm.gateway import CallRecord
from insightfulcell.services.llm_spend import DbSpendGuard
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders, OwnerSql
from tests.integration.runs_support import create_dataset, start_run

ADMIN_KEY = "a" * 48


def guard(
    services: Services,
    *,
    cap: str | None = "1.00",
    user_cap: str = "0",
    provider_caps: dict[str, str | None] | None = None,
) -> DbSpendGuard:
    return DbSpendGuard(
        services.db,
        cap_usd=None if cap is None else Decimal(cap),
        user_cap_usd=Decimal(user_cap),
        label="test",
        hold_seconds=600,
        usd_to_aud=Decimal("1.60"),
        provider_caps_usd={
            p: None if c is None else Decimal(c) for p, c in (provider_caps or {}).items()
        },
    )


def call(
    org_id: UUID, run_id: UUID | None, *, outcome: str = "ok", provider: str = "gemini"
) -> CallRecord:
    return CallRecord(
        org_id=org_id,
        run_id=run_id,
        purpose="plan",
        provider=provider,
        model="gemini-3.5-flash-lite",
        prompt_id="understand",
        prompt_version="understand-v1",
        attempt=1,
        outcome=outcome,
        input_tokens=1_000,
        output_tokens=200,
        cost_usd=Decimal("0.0008"),
        latency_ms=700,
    )


async def a_run(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[UUID, str]:
    dataset_id = (await create_dataset(client, headers))["dataset"]["id"]
    response = await start_run(client, headers, dataset_id)
    assert response.status_code == 202, response.text
    return UUID(response.json()["id"]), dataset_id


class TestSpendCap:
    async def test_reservations_and_spend_never_pass_the_cap(
        self, services: Services, alice: Identity
    ) -> None:
        spend = guard(services, cap="0.010")
        first = await spend.reserve(
            alice.org_id, None, "gemini", Decimal("0.006"), Decimal("0.001")
        )
        assert first.refused is None
        assert first.id is not None
        # Held, not yet spent: a second worst case that would pass the cap is refused.
        second = await spend.reserve(
            alice.org_id, None, "gemini", Decimal("0.006"), Decimal("0.001")
        )
        assert (second.id, second.refused) == (None, "spend_cap")
        # Settling with the real (smaller) cost frees the rest of the reservation.
        await spend.settle(first, call(alice.org_id, None), Decimal("0.002"), estimated=False)
        third = await spend.reserve(
            alice.org_id, None, "gemini", Decimal("0.006"), Decimal("0.001")
        )
        assert third.refused is None

    async def test_concurrent_reservations_cannot_overshoot(
        self, services: Services, alice: Identity
    ) -> None:
        spend = guard(services, cap="0.050")
        holds = await asyncio.gather(
            *(
                spend.reserve(alice.org_id, None, "gemini", Decimal("0.010"), Decimal(0))
                for _ in range(12)
            )
        )
        assert sum(1 for h in holds if h.refused is None) == 5
        assert {h.refused for h in holds if h.refused} == {"spend_cap"}

    async def test_a_person_can_have_a_cap_of_their_own(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, services: Services
    ) -> None:
        run_id, _ = await a_run(client, auth(alice))
        spend = guard(services, cap="1.00", user_cap="0.005")
        hold = await spend.reserve(alice.org_id, run_id, "gemini", Decimal("0.004"), Decimal(0))
        await spend.settle(hold, call(alice.org_id, run_id), Decimal("0.004"), estimated=False)
        refused = await spend.reserve(alice.org_id, run_id, "gemini", Decimal("0.004"), Decimal(0))
        assert refused.refused == "spend_cap_user"
        # Work that is not one person's run is only held to the overall cap.
        assert (
            await spend.reserve(alice.org_id, None, "gemini", Decimal("0.004"), Decimal(0))
        ).refused is None

    async def test_each_provider_has_its_own_cap_and_none_is_no_limit(
        self, services: Services, alice: Identity, owner_sql: OwnerSql
    ) -> None:
        spend = guard(services, cap=None, provider_caps={"gemini": None, "openai": "0.010"})
        # No overall cap and no Gemini cap: Gemini can spend past what OpenAI is allowed.
        gemini = await spend.reserve(alice.org_id, None, "gemini", Decimal("0.5"), Decimal(0))
        assert gemini.refused is None
        await spend.settle(gemini, call(alice.org_id, None), Decimal("0.5"), estimated=False)
        openai = await spend.reserve(alice.org_id, None, "openai", Decimal("0.006"), Decimal(0))
        assert openai.refused is None
        # Held for OpenAI: another worst case would pass OpenAI's cap (Gemini's spend is not
        # counted against it).
        refused = await spend.reserve(alice.org_id, None, "openai", Decimal("0.006"), Decimal(0))
        assert refused.refused == "spend_cap_provider"
        await spend.settle(
            openai, call(alice.org_id, None, provider="openai"), Decimal("0.004"), estimated=False
        )
        assert owner_sql(
            "SELECT provider, cost_usd, calls FROM app.llm_spend_provider_totals ORDER BY 1"
        ) == [("gemini", Decimal("0.500000"), 1), ("openai", Decimal("0.004000"), 1)]
        # The overall cap still covers every provider together when it is set.
        overall = guard(services, cap="0.6", provider_caps={"openai": "0.010"})
        assert (
            await overall.reserve(alice.org_id, None, "gemini", Decimal("0.2"), Decimal(0))
        ).refused == "spend_cap"

    async def test_an_abandoned_reservation_expires(
        self, services: Services, alice: Identity, owner_sql: OwnerSql
    ) -> None:
        spend = guard(services, cap="0.010")
        await spend.reserve(alice.org_id, None, "gemini", Decimal("0.010"), Decimal(0))
        assert (
            await spend.reserve(alice.org_id, None, "gemini", Decimal("0.001"), Decimal(0))
        ).refused
        owner_sql("UPDATE app.llm_spend_holds SET expires_at = now() - interval '1 second'")
        assert (
            await spend.reserve(alice.org_id, None, "gemini", Decimal("0.001"), Decimal(0))
        ).refused is None

    async def test_the_ledger_keeps_spend_after_the_run_and_dataset_are_deleted(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        services: Services,
        owner_sql: OwnerSql,
    ) -> None:
        headers = auth(alice)
        run_id, dataset_id = await a_run(client, headers)
        spend = guard(services)
        hold = await spend.reserve(alice.org_id, run_id, "gemini", Decimal("0.01"), Decimal(0))
        await spend.settle(hold, call(alice.org_id, run_id), Decimal("0.0008"), estimated=False)
        owner_sql(
            "UPDATE app.runs SET status = 'cancelled', finished_at = now() WHERE id = :id",
            id=run_id,
        )
        deleted = await client.delete(f"/v1/datasets/{dataset_id}", headers=headers)
        assert deleted.status_code == 200, deleted.text

        assert owner_sql("SELECT count(*) FROM app.runs WHERE id = :id", id=run_id) == [(0,)]
        assert owner_sql("SELECT user_id, cost_usd, label FROM app.llm_spend") == [
            (alice.user_id, Decimal("0.000800"), "test")
        ]
        assert owner_sql("SELECT uploads, uploads_completed, runs FROM app.user_activity") == [
            (1, 1, 1)
        ]
        with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
            owner_sql("DELETE FROM app.llm_spend")

    async def test_the_application_role_cannot_touch_the_ledger_directly(
        self, services: Services
    ) -> None:
        for statement in (
            "SELECT * FROM app.llm_spend",
            "SELECT * FROM app.llm_spend_totals",
            "SELECT * FROM app.llm_spend_provider_totals",
            "DELETE FROM app.llm_spend_holds",
            "UPDATE app.user_activity SET runs = 0",
        ):
            with pytest.raises(sqlalchemy.exc.DBAPIError, match="permission denied"):
                async with services.db.unscoped_transaction() as session:
                    await session.execute(text(statement))


@pytest.fixture
def admin_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"admin_dashboard_key": SecretStr(ADMIN_KEY)})


@pytest.fixture
async def admin_client(
    admin_settings: Settings, services: Services
) -> AsyncIterator[httpx.AsyncClient]:
    wired = build_services(
        admin_settings,
        engine=services.engine,
        store=S3ObjectStore(admin_settings),
        verifier=services.verifier,
        ticket_verifier=services.ticket_verifier,
    )
    app = create_app(admin_settings, services=wired)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as http,
    ):
        yield http


class TestAdminOverview:
    async def test_hidden_without_the_right_key(
        self,
        admin_client: httpx.AsyncClient,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
    ) -> None:
        for headers in ({}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ADMIN_KEY[:-1]}):
            response = await admin_client.get("/v1/admin/overview", headers=headers)
            assert response.status_code == 404
        # A signed-in person's token is not an admin key either.
        assert (
            await admin_client.get("/v1/admin/overview", headers=auth(alice))
        ).status_code == 404
        # Switched off (no key configured), the right-looking key finds nothing.
        off = await client.get("/v1/admin/overview", headers={"X-Admin-Key": ADMIN_KEY})
        assert off.status_code == 404
        assert "/v1/admin/overview" not in (await admin_client.get("/openapi.json")).text

    async def test_counts_people_files_runs_and_spend_without_their_content(
        self,
        admin_client: httpx.AsyncClient,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        services: Services,
    ) -> None:
        run_id, _ = await a_run(client, auth(alice))
        await create_dataset(client, auth(bob))
        spend = guard(services)
        hold = await spend.reserve(alice.org_id, run_id, "gemini", Decimal("0.01"), Decimal(0))
        await spend.settle(hold, call(alice.org_id, run_id), Decimal("0.0008"), estimated=False)

        response = await admin_client.get(
            "/v1/admin/overview", params={"days": 7}, headers={"X-Admin-Key": ADMIN_KEY}
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        users = {UUID(u["user_id"]): u for u in body["users"]}
        assert users.keys() == {alice.user_id, bob.user_id}
        assert (users[alice.user_id]["uploads"], users[alice.user_id]["runs_started"]) == (1, 1)
        assert float(users[alice.user_id]["cost_usd"]) == pytest.approx(0.0008)
        assert users[bob.user_id]["model_calls"] == 0
        assert float(body["spend"]["total_usd"]) == pytest.approx(0.0008)
        assert body["spend"]["by_model"][0]["model"] == "gemini-3.5-flash-lite"
        [gemini] = [p for p in body["spend"]["by_provider"] if p["provider"] == "gemini"]
        assert float(gemini["cost_usd"]) == pytest.approx(0.0008)
        assert gemini["calls"] == 1
        assert set(body["config"]["provider_spend_caps_aud"]) >= {"gemini", "openai"}
        assert body["organizations"] == 2
        assert body["config"]["label"] == "local"
        assert body["provider_reported"] == {"openai": {"status": "not_configured"}}
        # No file names, no keys.
        assert "sales-" not in response.text
        assert ADMIN_KEY not in response.text

    async def test_days_are_bounded(self, admin_client: httpx.AsyncClient) -> None:
        for days in (0, 366, "x"):
            response = await admin_client.get(
                "/v1/admin/overview", params={"days": days}, headers={"X-Admin-Key": ADMIN_KEY}
            )
            assert response.status_code == 422
