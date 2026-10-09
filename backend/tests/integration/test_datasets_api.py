from __future__ import annotations

import httpx
import pytest

from tests.conftest import Identity
from tests.integration.conftest import AuthHeaders
from tests.integration.helpers import csv_bytes, upload_file

pytestmark = pytest.mark.integration


async def _seed(client: httpx.AsyncClient, headers: dict[str, str], count: int) -> None:
    for i in range(count):
        response = await upload_file(
            client, headers, filename=f"set-{i:02d}.csv", data=csv_bytes(3, tag=f"-{i}")
        )
        assert response.status_code == 200, response.text


class TestListing:
    async def test_empty_organisation_gets_an_empty_page(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        body = (await client.get("/v1/datasets", headers=auth(alice))).json()
        assert body == {"items": [], "next_cursor": None}

    async def test_keyset_pagination_walks_every_item_once_newest_first(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        await _seed(client, auth(alice), 5)
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            body = (await client.get("/v1/datasets", params=params, headers=auth(alice))).json()
            seen += [item["name"] for item in body["items"]]
            pages += 1
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert pages == 3
        assert seen == [f"set-{i:02d}" for i in (4, 3, 2, 1, 0)]

    async def test_items_carry_latest_version(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        await _seed(client, auth(alice), 1)
        (item,) = (await client.get("/v1/datasets", headers=auth(alice))).json()["items"]
        assert item["version_count"] == 1
        assert item["latest_version"]["format"] == "csv"

    @pytest.mark.parametrize("cursor", ["!!!", "bm90LWpzb24", "WyJ4IiwieSJd"])
    async def test_invalid_cursor_is_422(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, cursor: str
    ) -> None:
        response = await client.get("/v1/datasets", params={"cursor": cursor}, headers=auth(alice))
        assert response.status_code == 422
        assert response.json()["code"] == "invalid-cursor"

    @pytest.mark.parametrize("limit", ["0", "-1", "101", "abc"])
    async def test_out_of_range_limit_is_422(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity, limit: str
    ) -> None:
        response = await client.get("/v1/datasets", params={"limit": limit}, headers=auth(alice))
        assert response.status_code == 422


class TestDetail:
    async def test_detail_lists_versions_newest_first(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        first = (await upload_file(client, auth(alice), filename="a.csv", data=csv_bytes(2))).json()
        dataset_id = first["dataset"]["id"]
        second = await upload_file(
            client, auth(alice), filename="a2.csv", data=csv_bytes(4), dataset_id=dataset_id
        )
        detail = (await client.get(f"/v1/datasets/{dataset_id}", headers=auth(alice))).json()
        assert [v["id"] for v in detail["versions"]] == [
            second.json()["version"]["id"],
            first["version"]["id"],
        ]
        assert detail["dataset"]["version_count"] == 2

    async def test_unknown_dataset_is_404_problem(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await client.get(
            "/v1/datasets/00000000-0000-7000-8000-000000000000", headers=auth(alice)
        )
        assert response.status_code == 404
        assert response.json()["code"] == "dataset-not-found"

    async def test_malformed_id_is_422(
        self, client: httpx.AsyncClient, auth: AuthHeaders, alice: Identity
    ) -> None:
        response = await client.get("/v1/datasets/not-a-uuid", headers=auth(alice))
        assert response.status_code == 422
