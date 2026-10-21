"""Integration fixtures: a real PostgreSQL database (migrated with Alembic, accessed as a
non-owner NOBYPASSRLS role, exactly like production) and an S3 emulator (moto server) that
accepts real HTTP, so presigned URLs are exercised end to end.

Set ``TEST_DATABASE_ADMIN_URL`` to a superuser URL, for example
``postgresql+psycopg://postgres@127.0.0.1:5432/postgres``. Without it the suite is skipped
locally and *fails* in CI (``CI=true``) so a misconfigured pipeline cannot pass silently.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from moto.server import ThreadedMotoServer
from pydantic import SecretStr
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.api.app import create_app
from insightfulcell.api.container import Services, build_services
from insightfulcell.auth.tokens import TokenVerifier
from insightfulcell.config import MIB, Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.storage.s3 import S3ObjectStore
from tests.conftest import AUDIENCE, ISSUER, Identity, MakeToken

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_ROLE_LOGIN = "ic_test_app"
APP_ROLE_PASSWORD = "ic_test_app_password"  # throwaway role in a throwaway database
BUCKET = "ic-test"
PART_BYTES = 5 * MIB

TENANT_TABLES = (
    # Not tenant tables, but spend and activity from one test must not count in the next.
    "app.llm_spend",
    "app.llm_spend_holds",
    "app.llm_spend_totals",
    "app.llm_spend_provider_totals",
    "app.user_activity",
    "app.usage_counters",
    "app.llm_calls",
    "app.dataset_preferences",
    "app.cleaning_ops",
    "app.cleaning_plans",
    "app.analysis_plans",
    "app.run_columns",
    "app.run_events",
    "app.run_stages",
    "app.runs",
    "app.idempotency_keys",
    "app.dataset_versions",
    "app.uploads",
    "app.datasets",
    "app.users",
    "app.organizations",
    # The job queue holds no tenant data, but jobs left by one test must not run in the next.
    "procrastinate.procrastinate_events",
    "procrastinate.procrastinate_periodic_defers",
    "procrastinate.procrastinate_jobs",
    "procrastinate.procrastinate_workers",
)


def _admin_url() -> URL:
    raw = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if not raw:
        message = "TEST_DATABASE_ADMIN_URL is not set"
        if os.environ.get("CI"):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    return make_url(raw)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- database


@pytest.fixture(scope="session")
def database_urls() -> Iterator[tuple[URL, URL]]:
    """Create a scratch database, migrate it as the owner, return (owner_url, app_url)."""
    admin = _admin_url()
    name = f"ic_test_{uuid.uuid4().hex[:12]}"
    admin_engine = create_sync_engine(admin, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner_url = admin.set(database=name)

    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = owner_url.render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config, "head")
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous

    with admin_engine.connect() as conn:
        conn.execute(
            text(
                f"""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE_LOGIN}') THEN
                        CREATE ROLE {APP_ROLE_LOGIN} LOGIN NOBYPASSRLS
                            PASSWORD '{APP_ROLE_PASSWORD}' IN ROLE ic_app;
                    END IF;
                END $$
                """
            )
        )
    app_url = owner_url.set(username=APP_ROLE_LOGIN, password=APP_ROLE_PASSWORD)
    try:
        yield owner_url, app_url
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(database_urls: tuple[URL, URL]) -> None:
    """Every test starts from empty tenant tables (TRUNCATE is not subject to RLS)."""
    owner_url, _ = database_urls
    engine = create_sync_engine(owner_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TENANT_TABLES)} CASCADE"))
    engine.dispose()


# --------------------------------------------------------------------------- object storage


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[str]:
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).create_bucket(Bucket=BUCKET)
    try:
        yield endpoint
    finally:
        server.stop()


@pytest.fixture
def raw_s3(s3_endpoint: str) -> S3Client:
    """Direct boto3 access to the emulator, for asserting on what was actually stored."""
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


# --------------------------------------------------------------------------- application


@pytest.fixture
def settings(database_urls: tuple[URL, URL], s3_endpoint: str) -> Settings:
    _, app_url = database_urls
    return Settings(
        environment="test",
        database_url=app_url.render_as_string(hide_password=False),
        auth_jwks_url=f"{ISSUER}/api/auth/jwks",
        auth_issuer=ISSUER,
        auth_audience=AUDIENCE,
        s3_bucket=BUCKET,
        s3_endpoint_url=s3_endpoint,
        s3_access_key_id=SecretStr("testing"),
        s3_secret_access_key=SecretStr("testing"),
        s3_force_path_style=True,
        max_upload_bytes=30 * MIB,
        upload_part_bytes=PART_BYTES,
        upload_expiry_hours=24,
        _env_file=None,
    )


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(settings)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def services(
    settings: Settings,
    engine: AsyncEngine,
    verifier: TokenVerifier,
    ticket_verifier: TokenVerifier,
) -> Services:
    return build_services(
        settings,
        engine=engine,
        store=S3ObjectStore(settings),
        verifier=verifier,
        ticket_verifier=ticket_verifier,
    )


@pytest.fixture
async def client(settings: Settings, services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as http,
    ):
        yield http


# --------------------------------------------------------------------------- helpers


OwnerSql = Callable[..., list[tuple[object, ...]]]


@pytest.fixture
def owner_sql(database_urls: tuple[URL, URL]) -> Iterator[OwnerSql]:
    """Run SQL as the table owner (bypasses RLS): for arranging state tests cannot reach
    through the API, such as backdating a row. Returns fetched rows (empty when none)."""
    owner_url, _ = database_urls
    engine = create_sync_engine(owner_url, isolation_level="AUTOCOMMIT")

    def _run(sql: str, **params: object) -> list[tuple[object, ...]]:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            return [tuple(row) for row in result] if result.returns_rows else []

    try:
        yield _run
    finally:
        engine.dispose()


AuthHeaders = Callable[..., dict[str, str]]


@pytest.fixture
def auth(make_token: MakeToken) -> AuthHeaders:
    """``auth(identity)`` -> Authorization header for that identity."""

    def _headers(identity: Identity, **claims: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(identity, **claims)}"}

    return _headers


@pytest.fixture
def alice() -> Identity:
    return Identity(user_id=uuid.uuid4(), org_id=uuid.uuid4(), role="owner", org_name="Acme")


@pytest.fixture
def bob() -> Identity:
    """A member of a *different* organisation."""
    return Identity(user_id=uuid.uuid4(), org_id=uuid.uuid4(), role="owner", org_name="Globex")
