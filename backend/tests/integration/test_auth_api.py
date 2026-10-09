from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import Identity, MakeToken
from tests.integration.conftest import AuthHeaders
from tests.integration.helpers import new_key

pytestmark = pytest.mark.integration


class TestAuthentication:
    async def test_health_needs_no_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/healthz")).json() == {"status": "ok"}

    async def test_ready_reports_current_schema(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    @pytest.mark.parametrize("path", ["/v1/me", "/v1/datasets", "/v1/uploads/" + "0" * 32])
    async def test_missing_token_is_401_problem(self, client: httpx.AsyncClient, path: str) -> None:
        response = await client.get(path)
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["code"] == "unauthenticated"

    async def test_invalid_token_is_401(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 401

    async def test_expired_token_is_401(
        self, client: httpx.AsyncClient, alice: Identity, make_token: MakeToken
    ) -> None:
        token = make_token(alice, exp=1, iat=0)
        response = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_security_headers_on_every_response(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]


class TestMe:
    async def test_returns_identity_and_provisions_mirrors(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        database_urls: tuple[object, object],
    ) -> None:
        response = await client.get("/v1/me", headers=auth(alice))
        assert response.status_code == 200
        assert response.json() == {
            "user_id": str(alice.user_id),
            "org_id": str(alice.org_id),
            "role": "owner",
            "org_name": "Acme",
        }

    async def test_repeated_calls_are_idempotent(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        for _ in range(3):
            assert (await client.get("/v1/me", headers=auth(alice))).status_code == 200


class TestProvisioningSurvivesRolledBackRequests:
    async def test_failed_first_request_does_not_poison_the_identity_cache(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        """A brand-new user's first request fails (404 rolls the transaction back, undoing the
        mirror inserts). The next request must still be able to create an upload."""
        missing = "00000000-0000-7000-8000-000000000000"
        assert (await client.get(f"/v1/uploads/{missing}", headers=auth(alice))).status_code == 404

        created = await client.post(
            "/v1/uploads",
            json={"filename": "a.csv", "size_bytes": 100},
            headers={**auth(alice), "Idempotency-Key": new_key()},
        )
        assert created.status_code == 201, created.text


class TestRoles:
    async def test_viewer_can_read_but_not_upload(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        viewer = Identity(alice.user_id, alice.org_id, role="viewer")
        assert (await client.get("/v1/datasets", headers=auth(viewer))).status_code == 200
        response = await client.post(
            "/v1/uploads",
            json={"filename": "a.csv", "size_bytes": 10},
            headers={**auth(viewer), "Idempotency-Key": new_key()},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"


async def test_mirror_rows_are_created(
    client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, engine: AsyncEngine
) -> None:
    await client.get("/v1/me", headers=auth(alice))
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.org_id', :o, false)"), {"o": str(alice.org_id)}
        )
        orgs = (await conn.execute(text("SELECT name FROM app.organizations"))).scalars().all()
        users = (await conn.execute(text("SELECT id FROM app.users"))).scalars().all()
    assert orgs == ["Acme"]
    assert users == [alice.user_id]
