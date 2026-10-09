from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import Identity
from tests.integration.conftest import PART_BYTES, AuthHeaders
from tests.integration.helpers import (
    complete,
    csv_bytes,
    new_key,
    put_parts,
    start_upload,
    upload_file,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration


class TestHappyPath:
    async def test_single_part_upload_creates_dataset_and_version(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, raw_s3: S3Client
    ) -> None:
        data = csv_bytes(50)
        response = await upload_file(client, auth(alice), filename="Home sales 2026.csv", data=data)

        assert response.status_code == 200, response.text
        body = response.json()
        digest = hashlib.sha256(data).hexdigest()
        assert body["duplicate"] is False
        assert body["dataset"]["name"] == "Home sales 2026"
        assert body["dataset"]["version_count"] == 1
        assert body["version"]["sha256"] == digest
        assert body["version"]["bytes"] == len(data)
        assert body["version"]["format"] == "csv"

        stored = raw_s3.get_object(Bucket="ic-test", Key=f"bronze/{alice.org_id}/{digest}")
        assert stored["Body"].read() == data

    async def test_multipart_upload_across_three_parts(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        # ~11 MiB of valid CSV: two full 5 MiB parts and a remainder.
        row = "1,hello world,3.14159\n"
        data = ("id,name,amount\n" + row * ((11 * 1024 * 1024) // len(row))).encode()
        started = await start_upload(client, auth(alice), filename="big.csv", size_bytes=len(data))
        ticket = started.json()
        assert ticket["part_count"] == 3
        assert [p["content_length"] for p in ticket["parts"]][:2] == [PART_BYTES, PART_BYTES]

        parts = await put_parts(ticket, data)
        response = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert response.status_code == 200, response.text
        assert response.json()["version"]["bytes"] == len(data)

    async def test_ticket_is_readable_and_pending(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        ticket = (await start_upload(client, auth(alice), filename="a.csv", size_bytes=10)).json()
        again = await client.get(f"/v1/uploads/{ticket['upload_id']}", headers=auth(alice))
        assert again.status_code == 200
        assert again.json()["status"] == "pending"
        assert len(again.json()["parts"]) == 1


class TestDeduplicationAndVersions:
    async def test_identical_bytes_return_existing_version(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        data = csv_bytes(10)
        first = (await upload_file(client, auth(alice), filename="a.csv", data=data)).json()
        second = (await upload_file(client, auth(alice), filename="copy.csv", data=data)).json()

        assert second["duplicate"] is True
        assert second["version"]["id"] == first["version"]["id"]
        listing = (await client.get("/v1/datasets", headers=auth(alice))).json()
        assert len(listing["items"]) == 1

    async def test_new_bytes_can_become_a_new_version_of_a_dataset(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        first = (await upload_file(client, auth(alice), filename="a.csv", data=csv_bytes(5))).json()
        dataset_id = first["dataset"]["id"]
        second = await upload_file(
            client,
            auth(alice),
            filename="a-v2.csv",
            data=csv_bytes(6),
            dataset_id=dataset_id,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["dataset"]["id"] == dataset_id
        assert body["dataset"]["version_count"] == 2
        assert body["duplicate"] is False

    async def test_upload_to_unknown_dataset_is_404(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await start_upload(
            client,
            auth(alice),
            filename="a.csv",
            size_bytes=10,
            dataset_id="00000000-0000-7000-8000-000000000000",
        )
        assert response.status_code == 404
        assert response.json()["code"] == "dataset-not-found"


class TestIdempotency:
    async def test_replay_returns_same_upload_with_fresh_urls(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        key = new_key()
        first = await start_upload(client, auth(alice), filename="a.csv", size_bytes=10, key=key)
        second = await start_upload(client, auth(alice), filename="a.csv", size_bytes=10, key=key)
        assert first.status_code == second.status_code == 201
        assert first.json()["upload_id"] == second.json()["upload_id"]

    async def test_same_key_with_different_request_is_rejected(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        key = new_key()
        await start_upload(client, auth(alice), filename="a.csv", size_bytes=10, key=key)
        reused = await start_upload(client, auth(alice), filename="b.csv", size_bytes=10, key=key)
        assert reused.status_code == 422
        assert reused.json()["code"] == "idempotency-key-reused"

    async def test_idempotency_key_is_required_and_validated(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        missing = await client.post(
            "/v1/uploads", json={"filename": "a.csv", "size_bytes": 10}, headers=auth(alice)
        )
        assert missing.status_code == 422
        short = await client.post(
            "/v1/uploads",
            json={"filename": "a.csv", "size_bytes": 10},
            headers={**auth(alice), "Idempotency-Key": "short"},
        )
        assert short.status_code == 422

    async def test_completing_twice_returns_the_same_version(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        data = csv_bytes(5)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        parts = await put_parts(ticket, data)
        first = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        second = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert first.status_code == second.status_code == 200
        assert first.json()["version"]["id"] == second.json()["version"]["id"]
        assert second.json()["duplicate"] is False


class TestValidation:
    @pytest.mark.parametrize("filename", ["notes.txt", "archive.zip", "noextension"])
    async def test_unsupported_file_type(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, filename: str
    ) -> None:
        response = await start_upload(client, auth(alice), filename=filename, size_bytes=10)
        assert response.status_code == 422
        assert response.json()["code"] == "unsupported-file-type"

    async def test_legacy_xls_gets_a_hint(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await start_upload(client, auth(alice), filename="old.xls", size_bytes=10)
        assert ".xlsx" in response.json()["hint"]

    async def test_file_over_limit(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await start_upload(
            client, auth(alice), filename="huge.csv", size_bytes=31 * 1024 * 1024
        )
        assert response.status_code == 422
        assert response.json()["code"] == "file-too-large"

    async def test_unknown_fields_and_zero_size_are_rejected(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        for payload in (
            {"filename": "a.csv", "size_bytes": 0},
            {"filename": "a.csv", "size_bytes": 5, "org_id": "leaky-value-123"},
        ):
            response = await client.post(
                "/v1/uploads", json=payload, headers={**auth(alice), "Idempotency-Key": new_key()}
            )
            assert response.status_code == 422
            assert "leaky-value-123" not in response.text  # caller input is never echoed back

    async def test_binary_content_with_csv_extension_is_rejected_and_aborted(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        data = b"%PDF-1.7\n" + b"\x00" * 200
        ticket = (
            await start_upload(client, auth(alice), filename="fake.csv", size_bytes=len(data))
        ).json()
        parts = await put_parts(ticket, data)
        response = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert response.status_code == 422
        assert response.json()["code"] == "content-mismatch"

        status = await client.get(f"/v1/uploads/{ticket['upload_id']}", headers=auth(alice))
        assert status.json()["status"] == "aborted"  # the state change survived the error

    async def test_size_mismatch_aborts(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        declared = csv_bytes(5)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(declared))
        ).json()
        # The presigned URL signs ContentLength, but the emulator does not enforce signatures, so
        # sending different bytes simulates a client that lied about the size.
        actual = csv_bytes(6)
        async with httpx.AsyncClient() as storage:
            put = await storage.put(ticket["parts"][0]["url"], content=actual)
        parts = [{"part_number": 1, "etag": put.headers["ETag"]}]
        response = await complete(client, auth(alice), ticket["upload_id"], parts, actual)
        assert response.status_code == 422
        assert response.json()["code"] == "upload-size-mismatch"

    @pytest.mark.parametrize(
        "parts",
        [
            [{"part_number": 2, "etag": '"x"'}],
            [{"part_number": 1, "etag": '"x"'}, {"part_number": 1, "etag": '"x"'}],
            [],
        ],
    )
    async def test_invalid_part_lists(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        parts: list[dict[str, Any]],
    ) -> None:
        data = csv_bytes(3)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        response = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert response.status_code in (409, 422)
        assert response.json()["code"] in {"invalid-parts", "invalid-request"}

    async def test_malformed_checksum(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        data = csv_bytes(3)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        parts = await put_parts(ticket, data)
        response = await complete(
            client, auth(alice), ticket["upload_id"], parts, data, sha256="abc"
        )
        assert response.status_code == 422


class TestLifecycle:
    async def test_abort_then_complete_conflicts(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        data = csv_bytes(3)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        parts = await put_parts(ticket, data)
        aborted = await client.post(f"/v1/uploads/{ticket['upload_id']}:abort", headers=auth(alice))
        assert aborted.status_code == 204
        again = await client.post(f"/v1/uploads/{ticket['upload_id']}:abort", headers=auth(alice))
        assert again.status_code == 204  # abort is idempotent

        response = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert response.status_code == 409
        assert response.json()["code"] == "upload-not-pending"

    async def test_expired_upload_cannot_be_completed(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        engine: AsyncEngine,
        raw_s3: S3Client,
    ) -> None:
        data = csv_bytes(3)
        ticket = (
            await start_upload(client, auth(alice), filename="a.csv", size_bytes=len(data))
        ).json()
        parts = await put_parts(ticket, data)
        old = datetime.now(UTC) - timedelta(hours=48)
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.org_id', :o, true)"), {"o": str(alice.org_id)}
            )
            await conn.execute(
                text("UPDATE app.uploads SET created_at = :t WHERE id = :i"),
                {"t": old, "i": ticket["upload_id"]},
            )
        response = await complete(client, auth(alice), ticket["upload_id"], parts, data)
        assert response.status_code == 409
        assert response.json()["code"] == "upload-expired"
        status = await client.get(f"/v1/uploads/{ticket['upload_id']}", headers=auth(alice))
        assert status.json()["status"] == "expired"
        # The uploaded parts were released, not left behind as an unfinished multipart upload.
        pending = raw_s3.list_multipart_uploads(
            Bucket="ic-test", Prefix=f"bronze/{alice.org_id}/uploads/{ticket['upload_id']}"
        )
        assert pending.get("Uploads", []) == []

    async def test_presign_parts_gives_fresh_urls_for_requested_parts(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        size = 11 * 1024 * 1024
        ticket = (await start_upload(client, auth(alice), filename="a.csv", size_bytes=size)).json()
        response = await client.post(
            f"/v1/uploads/{ticket['upload_id']}:presign-parts",
            json={"part_numbers": [3, 2, 2]},
            headers=auth(alice),
        )
        assert response.status_code == 200
        assert [p["part_number"] for p in response.json()["parts"]] == [2, 3]

    @pytest.mark.parametrize("numbers", [[0], [4], [-1]])
    async def test_presign_parts_rejects_out_of_range(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, numbers: list[int]
    ) -> None:
        size = 11 * 1024 * 1024
        ticket = (await start_upload(client, auth(alice), filename="a.csv", size_bytes=size)).json()
        response = await client.post(
            f"/v1/uploads/{ticket['upload_id']}:presign-parts",
            json={"part_numbers": numbers},
            headers=auth(alice),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid-parts"


class TestLogsDoNotLeakCredentials:
    async def test_presigned_urls_are_not_stored(
        self,
        client: httpx.AsyncClient,
        auth: AuthHeaders,
        alice: Identity,
        engine: AsyncEngine,
    ) -> None:
        ticket = (await start_upload(client, auth(alice), filename="a.csv", size_bytes=10)).json()
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.org_id', :o, true)"), {"o": str(alice.org_id)}
            )
            body = (
                await conn.execute(text("SELECT response_body::text FROM app.idempotency_keys"))
            ).scalar_one()
        assert "X-Amz-Signature" not in body
        assert ticket["parts"][0]["url"] not in body
