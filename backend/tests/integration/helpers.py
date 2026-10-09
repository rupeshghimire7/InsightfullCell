"""Drive the upload flow the way the browser does: create, PUT each part, complete."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

Headers = dict[str, str]


@dataclass(frozen=True)
class Started:
    body: dict[str, Any]

    @property
    def upload_id(self) -> str:
        return str(self.body["upload_id"])


def new_key() -> str:
    return f"test-{uuid.uuid4().hex}"


async def start_upload(
    client: httpx.AsyncClient,
    headers: Headers,
    *,
    filename: str,
    size_bytes: int,
    dataset_id: str | None = None,
    key: str | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {"filename": filename, "size_bytes": size_bytes}
    if dataset_id is not None:
        payload["dataset_id"] = dataset_id
    return await client.post(
        "/v1/uploads", json=payload, headers={**headers, "Idempotency-Key": key or new_key()}
    )


async def put_parts(ticket: dict[str, Any], data: bytes) -> list[dict[str, Any]]:
    """PUT every part to its presigned URL and return the ``parts`` list for completion."""
    completed = []
    async with httpx.AsyncClient(timeout=30) as storage:
        for part in ticket["parts"]:
            start = (part["part_number"] - 1) * ticket["part_size_bytes"]
            chunk = data[start : start + part["content_length"]]
            assert len(chunk) == part["content_length"]
            response = await storage.put(part["url"], content=chunk)
            response.raise_for_status()
            completed.append({"part_number": part["part_number"], "etag": response.headers["ETag"]})
    return completed


async def complete(
    client: httpx.AsyncClient,
    headers: Headers,
    upload_id: str,
    parts: list[dict[str, Any]],
    data: bytes,
    *,
    sha256: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/v1/uploads/{upload_id}:complete",
        json={"sha256": sha256 or hashlib.sha256(data).hexdigest(), "parts": parts},
        headers=headers,
    )


async def upload_file(
    client: httpx.AsyncClient, headers: Headers, *, filename: str, data: bytes, **kwargs: Any
) -> httpx.Response:
    """Whole happy path; returns the ``:complete`` response."""
    started = await start_upload(client, headers, filename=filename, size_bytes=len(data), **kwargs)
    assert started.status_code == 201, started.text
    ticket = started.json()
    parts = await put_parts(ticket, data)
    return await complete(client, headers, ticket["upload_id"], parts, data)


def csv_bytes(rows: int = 3, tag: str = "") -> bytes:
    lines = ["id,name,amount"] + [f"{i},item{i}{tag},{i * 1.5}" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()
