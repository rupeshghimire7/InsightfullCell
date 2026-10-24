from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.api.app import create_app
from insightfulcell.api.container import Services, build_services
from insightfulcell.config import Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders

pytestmark = pytest.mark.integration


@pytest.fixture
async def unreachable_client(
    settings: Settings, verifier: object
) -> AsyncIterator[httpx.AsyncClient]:
    broken = settings.model_copy(
        update={"database_url": "postgresql+psycopg://nobody@127.0.0.1:1/none"}
    )
    engine = create_engine(broken)
    services = build_services(
        broken,
        engine=engine,
        store=S3ObjectStore(broken),
        verifier=verifier,  # type: ignore[arg-type]
    )
    app = create_app(broken, services=services)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as http,
    ):
        yield http
    await engine.dispose()


async def test_readiness_fails_with_503_when_database_is_down(
    unreachable_client: httpx.AsyncClient,
) -> None:
    response = await unreachable_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"] == "database-unreachable"
    # liveness is independent of dependencies
    assert (await unreachable_client.get("/healthz")).status_code == 200


async def test_unexpected_errors_become_generic_500_without_leaking_details(
    settings: Settings, services: Services, engine: AsyncEngine
) -> None:
    app: FastAPI = create_app(settings, services=services)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret connection string postgres://user:pw@host")

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://api.test",
        ) as http,
    ):
        response = await http.get("/boom")
    assert response.status_code == 500
    assert response.json()["code"] == "internal-error"
    assert "secret" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


async def test_unknown_route_and_method_are_problem_json(
    client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
) -> None:
    missing = await client.get("/v1/nope", headers=auth(alice))
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    wrong_method = await client.put("/v1/me", headers=auth(alice))
    assert wrong_method.status_code == 405
    assert wrong_method.json()["code"] == "http-405"


async def test_storage_outage_surfaces_as_503_and_leaves_no_upload_row(
    settings: Settings, engine: AsyncEngine, verifier: object, auth: AuthHeaders, alice: Identity
) -> None:
    dead = settings.model_copy(update={"s3_endpoint_url": "http://127.0.0.1:1"})
    services = build_services(
        dead,
        engine=engine,
        store=S3ObjectStore(dead),
        verifier=verifier,  # type: ignore[arg-type]
    )
    app = create_app(dead, services=services)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as http,
    ):
        response = await http.post(
            "/v1/uploads",
            json={"filename": "a.csv", "size_bytes": 10},
            headers={**auth(alice), "Idempotency-Key": "key-for-outage-test"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "storage-unavailable"
        listing = await http.get("/v1/datasets", headers=auth(alice))
        assert listing.status_code == 200
