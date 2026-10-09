"""Cross-tenant isolation (NFR-SEC-01). The most important suite in the repository.

Two layers are tested independently, because either one alone must hold:
1. the API never reveals or touches another organisation's data (404, never 403);
2. PostgreSQL itself refuses, via forced RLS, even when application code forgets a filter.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.config import Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.db.revision import EXPECTED_SCHEMA_REVISION
from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders
from tests.integration.helpers import (
    complete,
    csv_bytes,
    put_parts,
    start_upload,
    upload_file,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = [pytest.mark.integration, pytest.mark.tenancy]


async def _run_in_tenant(
    engine: AsyncEngine, org_id: uuid.UUID, sql: str, params: dict[str, Any] | None = None
) -> None:
    """Execute one statement inside a transaction scoped to ``org_id`` (committed on success)."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
        await conn.execute(text(sql), params or {})


def _run_as_owner(owner_url: URL, sql: str, params: dict[str, Any]) -> None:
    """Execute one statement as the table owner (a superuser: RLS does not apply)."""
    owner = create_sync_engine(owner_url)
    try:
        with owner.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        owner.dispose()


# --------------------------------------------------------------------------- API layer


class TestApiIsolation:
    async def test_other_org_cannot_read_a_dataset(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        created = (
            await upload_file(client, auth(alice), filename="secret.csv", data=csv_bytes(3))
        ).json()
        dataset_id = created["dataset"]["id"]

        response = await client.get(f"/v1/datasets/{dataset_id}", headers=auth(bob))
        assert response.status_code == 404  # not 403: existence is not revealed
        assert response.json()["code"] == "dataset-not-found"
        assert "secret" not in response.text

    async def test_listing_only_contains_own_datasets(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        await upload_file(client, auth(alice), filename="alice.csv", data=csv_bytes(3, "a"))
        await upload_file(client, auth(bob), filename="bob.csv", data=csv_bytes(3, "b"))

        alice_names = [
            i["name"]
            for i in (await client.get("/v1/datasets", headers=auth(alice))).json()["items"]
        ]
        bob_names = [
            i["name"] for i in (await client.get("/v1/datasets", headers=auth(bob))).json()["items"]
        ]
        assert alice_names == ["alice"]
        assert bob_names == ["bob"]

    async def test_other_org_cannot_touch_an_upload(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        data = csv_bytes(3)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        upload_id = ticket["upload_id"]
        parts = await put_parts(ticket, data)
        await client.get("/v1/me", headers=auth(bob))  # make sure bob exists

        attempts = [
            await client.get(f"/v1/uploads/{upload_id}", headers=auth(bob)),
            await client.post(
                f"/v1/uploads/{upload_id}:presign-parts",
                json={"part_numbers": [1]},
                headers=auth(bob),
            ),
            await complete(client, auth(bob), upload_id, parts, data),
            await client.post(f"/v1/uploads/{upload_id}:abort", headers=auth(bob)),
        ]
        assert [r.status_code for r in attempts] == [404, 404, 404, 404]
        assert {r.json()["code"] for r in attempts} == {"upload-not-found"}

        # and the owner's upload is untouched
        assert (await client.get(f"/v1/uploads/{upload_id}", headers=auth(alice))).json()[
            "status"
        ] == "pending"

    async def test_cannot_add_a_version_to_another_orgs_dataset(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        created = (
            await upload_file(client, auth(alice), filename="a.csv", data=csv_bytes(3))
        ).json()
        response = await start_upload(
            client,
            auth(bob),
            filename="b.csv",
            size_bytes=10,
            dataset_id=created["dataset"]["id"],
        )
        assert response.status_code == 404
        assert response.json()["code"] == "dataset-not-found"

    async def test_identical_bytes_are_not_deduplicated_across_organisations(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        data = csv_bytes(7)
        first = (await upload_file(client, auth(alice), filename="x.csv", data=data)).json()
        second = (await upload_file(client, auth(bob), filename="x.csv", data=data)).json()

        assert second["duplicate"] is False  # would otherwise leak that alice has this file
        assert second["version"]["id"] != first["version"]["id"]
        assert second["dataset"]["id"] != first["dataset"]["id"]

    async def test_idempotency_keys_are_scoped_to_the_organisation(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, bob: Identity
    ) -> None:
        key = f"shared-key-{uuid.uuid4().hex}"
        a = await start_upload(client, auth(alice), filename="a.csv", size_bytes=10, key=key)
        b = await start_upload(client, auth(bob), filename="a.csv", size_bytes=10, key=key)
        assert a.status_code == b.status_code == 201
        assert a.json()["upload_id"] != b.json()["upload_id"]

    async def test_object_keys_are_namespaced_by_organisation(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, raw_s3: S3Client
    ) -> None:
        data = csv_bytes(4)
        body = (await upload_file(client, auth(alice), filename="a.csv", data=data)).json()
        keys = [
            o["Key"]
            for o in raw_s3.list_objects_v2(Bucket="ic-test", Prefix=f"bronze/{alice.org_id}/")[
                "Contents"
            ]
        ]
        assert keys == [f"bronze/{alice.org_id}/{body['version']['sha256']}"]


# --------------------------------------------------------------------------- database layer


async def _seed_dataset(client: httpx.AsyncClient, auth: AuthHeaders, who: Identity) -> str:
    body = (await upload_file(client, auth(who), filename="seed.csv", data=csv_bytes(3))).json()
    return str(body["dataset"]["id"])


class TestRowLevelSecurity:
    async def test_test_role_is_subject_to_rls(self, engine: AsyncEngine) -> None:
        """Guard: if this fails, every other test in this class proves nothing."""
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
            owns = (
                await conn.execute(
                    text("SELECT tableowner FROM pg_tables WHERE tablename = 'datasets'")
                )
            ).scalar_one()
            current = (await conn.execute(text("SELECT current_user"))).scalar_one()
        assert tuple(row) == (False, False)
        assert owns != current

    async def test_no_tenant_context_sees_nothing(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        engine: AsyncEngine,
    ) -> None:
        await _seed_dataset(client, auth, alice)
        async with engine.connect() as conn:
            for table in (
                "datasets",
                "uploads",
                "dataset_versions",
                "idempotency_keys",
                "organizations",
            ):
                count = (await conn.execute(text(f"SELECT count(*) FROM app.{table}"))).scalar_one()
                assert count == 0, table  # fails closed: absent setting => no rows

    async def test_wrong_tenant_sees_nothing_right_tenant_sees_rows(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        engine: AsyncEngine,
    ) -> None:
        await _seed_dataset(client, auth, alice)
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.org_id', :o, true)"), {"o": str(bob.org_id)}
            )
            assert (await conn.execute(text("SELECT count(*) FROM app.datasets"))).scalar_one() == 0
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.org_id', :o, true)"), {"o": str(alice.org_id)}
            )
            assert (await conn.execute(text("SELECT count(*) FROM app.datasets"))).scalar_one() == 1

    async def test_empty_string_setting_also_fails_closed(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, engine: AsyncEngine
    ) -> None:
        await _seed_dataset(client, auth, alice)
        async with engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.org_id', '', true)"))
            assert (await conn.execute(text("SELECT count(*) FROM app.datasets"))).scalar_one() == 0

    async def test_cannot_write_a_row_for_another_organisation(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        engine: AsyncEngine,
    ) -> None:
        await client.get("/v1/me", headers=auth(alice))
        await client.get("/v1/me", headers=auth(bob))
        with pytest.raises(DBAPIError, match="row-level security"):
            await _run_in_tenant(
                engine,
                alice.org_id,
                "INSERT INTO app.datasets (org_id, name, created_by) "
                "VALUES (:org, 'planted', :user)",
                {"org": bob.org_id, "user": alice.user_id},
            )

    async def test_cannot_move_a_row_to_another_organisation(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        engine: AsyncEngine,
    ) -> None:
        await _seed_dataset(client, auth, alice)
        await client.get("/v1/me", headers=auth(bob))
        with pytest.raises(DBAPIError, match="row-level security"):
            await _run_in_tenant(
                engine, alice.org_id, "UPDATE app.datasets SET org_id = :bob", {"bob": bob.org_id}
            )

    async def test_tenant_setting_does_not_leak_through_the_pool(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        settings: Settings,
    ) -> None:
        await _seed_dataset(client, auth, alice)
        single = create_engine(settings.model_copy(update={"db_pool_size": 1}))
        try:
            async with single.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.org_id', :o, true)"), {"o": str(alice.org_id)}
                )
                assert (
                    await conn.execute(text("SELECT count(*) FROM app.datasets"))
                ).scalar_one() == 1
            # The very same pooled connection is reused: the setting must be gone.
            async with single.connect() as conn:
                leaked = (
                    await conn.execute(text("SELECT current_setting('app.org_id', true)"))
                ).scalar_one()
                assert leaked in (None, "")
                assert (
                    await conn.execute(text("SELECT count(*) FROM app.datasets"))
                ).scalar_one() == 0
        finally:
            await single.dispose()


class TestLeastPrivilege:
    async def test_application_role_cannot_modify_or_delete_dataset_versions(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, engine: AsyncEngine
    ) -> None:
        await _seed_dataset(client, auth, alice)
        for statement in (
            "UPDATE app.dataset_versions SET bytes = 1",
            "DELETE FROM app.dataset_versions",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"):
                await _run_in_tenant(engine, alice.org_id, statement)

    async def test_application_role_cannot_create_or_drop_tables(self, engine: AsyncEngine) -> None:
        for statement in ("CREATE TABLE app.evil (id int)", "DROP TABLE app.datasets"):
            with pytest.raises(ProgrammingError):
                await _run_in_tenant(engine, uuid.uuid4(), statement)

    async def test_application_role_can_read_schema_revision_only_from_public(
        self, engine: AsyncEngine
    ) -> None:
        async with engine.connect() as conn:
            revision = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        assert revision == EXPECTED_SCHEMA_REVISION


class TestCompositeForeignKeys:
    """Even a privileged writer that bypasses RLS cannot link rows across organisations."""

    async def test_upload_cannot_reference_another_orgs_dataset(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        database_urls: tuple[URL, URL],
    ) -> None:
        dataset_id = await _seed_dataset(client, auth, alice)
        await client.get("/v1/me", headers=auth(bob))
        owner_url, _ = database_urls
        with pytest.raises(IntegrityError, match="foreign key"):
            _run_as_owner(
                owner_url,
                """
                INSERT INTO app.uploads
                    (org_id, dataset_id, created_by, filename, format, declared_bytes,
                     part_size_bytes, part_count, staging_key)
                VALUES (:org, :ds, :usr, 'x.csv', 'csv', 10, 5242880, 1, 'k')
                """,
                {"org": bob.org_id, "ds": dataset_id, "usr": bob.user_id},
            )

    async def test_version_cannot_reference_another_orgs_dataset(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        bob: Identity,
        database_urls: tuple[URL, URL],
    ) -> None:
        dataset_id = await _seed_dataset(client, auth, alice)
        await client.get("/v1/me", headers=auth(bob))
        owner_url, _ = database_urls
        with pytest.raises(IntegrityError, match="foreign key"):
            _run_as_owner(
                owner_url,
                """
                INSERT INTO app.dataset_versions
                    (org_id, dataset_id, sha256, bytes, format, bronze_key, created_by)
                VALUES (:org, :ds, decode(repeat('ab', 32), 'hex'), 5, 'csv', 'k', :usr)
                """,
                {"org": bob.org_id, "ds": dataset_id, "usr": bob.user_id},
            )
