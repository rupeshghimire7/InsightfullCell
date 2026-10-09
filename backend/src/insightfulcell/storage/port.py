"""Object storage port: what the services need, independent of S3 (ADR-009)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CompletedPart:
    part_number: int
    etag: str


@dataclass(frozen=True, slots=True)
class PartSpec:
    """One part to presign. ``content_length`` is signed into the URL, so storage itself
    rejects a part whose size differs from what the upload declared."""

    part_number: int
    content_length: int


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    etag: str


class MultipartNotFoundError(Exception):
    """The multipart upload no longer exists (already completed, aborted or expired)."""


class ObjectNotFoundError(Exception):
    """The object does not exist (for example, it was purged by the retention policy)."""


class InvalidPartsError(Exception):
    """Storage refused the part list (missing part, wrong ETag, part too small)."""


class ObjectStore(Protocol):
    async def create_multipart(self, key: str) -> str:
        """Start a multipart upload and return its upload id."""

    def presign_parts(
        self, key: str, upload_id: str, parts: Sequence[PartSpec], *, expires_in: int
    ) -> Mapping[int, str]:
        """Presign PUT URLs for parts. Pure computation: no network call."""

    async def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[CompletedPart]
    ) -> None: ...

    async def abort_multipart(self, key: str, upload_id: str) -> None: ...

    async def head(self, key: str) -> ObjectInfo | None:
        """Metadata for an object, or ``None`` if it does not exist."""

    async def read_range(self, key: str, start: int, end: int) -> bytes:
        """Read bytes ``start..end`` inclusive."""

    async def copy(self, source_key: str, dest_key: str) -> None: ...

    async def delete(self, key: str) -> None:
        """Delete an object; deleting a missing object is not an error."""

    async def download_file(self, key: str, dest: Path) -> None:
        """Stream an object to a local file (the worker reads uploads this way). Raises
        ``ObjectNotFoundError`` when there is no such object."""

    async def upload_file(self, source: Path, key: str) -> None:
        """Store a local file under ``key`` (multipart for large files)."""
