"""A database outage is a retryable 503 problem, not an unexpected 500."""

from __future__ import annotations

import socket
from uuid import uuid4

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from insightfulcell.api.app import create_app
from insightfulcell.api.container import build_services
from insightfulcell.auth.tokens import TokenVerifier
from insightfulcell.config import Settings
from insightfulcell.db.tenant import TenantDatabase, is_connection_failure
from insightfulcell.domain.errors import DatabaseUnavailable
from insightfulcell.domain.security import Principal, Role
from tests.conftest import AUDIENCE, ISSUER, MakeToken


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _principal() -> Principal:
    return Principal(user_id=uuid4(), org_id=uuid4(), role=Role.OWNER, org_name=None)


async def test_refused_connection_becomes_database_unavailable() -> None:
    engine = create_async_engine(
        f"postgresql+psycopg://nobody:nothing@127.0.0.1:{_closed_port()}/none",
        connect_args={"connect_timeout": 2},
    )
    try:
        with pytest.raises(DatabaseUnavailable) as caught:
            async with TenantDatabase(engine).transaction(_principal()):
                pass  # pragma: no cover - never reached
    finally:
        await engine.dispose()
    assert caught.value.code == "database-unavailable"
    assert "nobody" not in caught.value.detail  # no connection details in user-facing text


def _wrap(orig: Exception, cls: type[DBAPIError] = OperationalError) -> DBAPIError:
    return cls("SELECT 1", {}, orig)


def test_classifies_connection_failures_only() -> None:
    assert is_connection_failure(_wrap(psycopg.OperationalError("connection refused")))
    assert is_connection_failure(_wrap(psycopg.errors.AdminShutdown()))
    assert not is_connection_failure(_wrap(psycopg.errors.QueryCanceled()))  # statement timeout
    assert not is_connection_failure(_wrap(psycopg.errors.UniqueViolation(), cls=IntegrityError))


async def test_api_answers_503_problem(verifier: TokenVerifier, make_token: MakeToken) -> None:
    port = _closed_port()
    settings = Settings(
        environment="test",
        database_url=f"postgresql+psycopg://nobody:nothing@127.0.0.1:{port}/none",
        auth_jwks_url=f"{ISSUER}/api/auth/jwks",
        auth_issuer=ISSUER,
        auth_audience=AUDIENCE,
        s3_endpoint_url="http://127.0.0.1:1",
        _env_file=None,
    )
    services = build_services(settings, verifier=verifier)
    app = create_app(settings, services=services)
    app.state.services = services  # httpx's transport does not run the lifespan
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/datasets", headers={"Authorization": f"Bearer {make_token()}"}
            )
    finally:
        await services.engine.dispose()
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "database-unavailable"
    assert body["request_id"]
    assert "nobody" not in response.text
