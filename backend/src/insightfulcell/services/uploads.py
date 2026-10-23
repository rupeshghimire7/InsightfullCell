"""Upload use cases (IC-006): create, presign, complete, abort.

The browser uploads parts straight to object storage (ADR-009), so this service never touches
file bytes beyond the first few KiB it sniffs. Integrity of the *whole file* is verified by the
ingestion worker, which re-hashes before reading (ADR-026); here we verify what is cheap and
what protects storage: size, signature and part list.

Failure ordering rule: a state change (``aborted``, ``expired``) must commit *before* the error
is raised, otherwise the rollback would undo the very record of why the upload died. Services
therefore return an ``_Outcome`` from inside the transaction and raise after it commits.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.config import Settings
from insightfulcell.db.models import Dataset, DatasetVersion, StoragePurge, Upload
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import (
    ContentMismatch,
    DatasetNotFound,
    DomainError,
    FileTooLarge,
    InvalidParts,
    UploadExpired,
    UploadNotFound,
    UploadNotPending,
    UploadSizeMismatch,
)
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.ingestion.formats import (
    SNIFF_BYTES,
    FileFormat,
    dataset_name_from_filename,
    format_from_filename,
    sanitize_filename,
    sniff,
)
from insightfulcell.services import idempotency
from insightfulcell.services.dto import (
    CompletedUpload,
    DatasetInfo,
    PartTarget,
    UploadTicket,
)
from insightfulcell.services.queries import load_dataset_info, to_version_info
from insightfulcell.storage.port import (
    CompletedPart,
    InvalidPartsError,
    MultipartNotFoundError,
    ObjectStore,
    PartSpec,
)

logger = logging.getLogger(__name__)

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_IDEMPOTENCY_PATH = "/v1/uploads"
_MAX_PRESIGN_BATCH = 100


@dataclass(slots=True)
class _Outcome:
    """What happened inside a transaction: raised only after the transaction committed."""

    result: CompletedUpload | None = None
    error: DomainError | None = None
    cleanup_keys: list[str] = field(default_factory=list)
    # (key, multipart upload id) of a multipart upload to abort: its parts are not an object yet,
    # so deleting the key would leave them in storage.
    abort_multipart: tuple[str, str] | None = None


class UploadService:
    def __init__(
        self,
        db: TenantDatabase,
        store: ObjectStore,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._store = store
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ create

    async def create_upload(
        self,
        principal: Principal,
        *,
        filename: str,
        size_bytes: int,
        dataset_id: UUID | None,
        idempotency_key: str,
    ) -> UploadTicket:
        require(principal, Action.UPLOAD_CREATE)
        name = sanitize_filename(filename)
        fmt = format_from_filename(name)  # validates the extension before anything is stored
        if size_bytes > self._settings.max_upload_bytes:
            limit_mb = self._settings.max_upload_bytes // (1024 * 1024)
            raise FileTooLarge(
                f"The file is {size_bytes / 1024 / 1024:,.0f} MB; the limit is {limit_mb} MB.",
                hint="Try Parquet for large data, or upload an aggregated extract.",
            )
        part_size = self._settings.upload_part_bytes
        part_count = max(1, math.ceil(size_bytes / part_size))
        digest = idempotency.request_hash(
            {
                "filename": name,
                "size_bytes": size_bytes,
                "dataset_id": str(dataset_id) if dataset_id else None,
            }
        )

        async with self._db.transaction(principal) as session:
            replay = await idempotency.claim(
                session, principal, key=idempotency_key, path=_IDEMPOTENCY_PATH, digest=digest
            )
            if replay is not None:
                upload = await self._load_upload(session, principal, UUID(str(replay["upload_id"])))
                return self._ticket(upload)

            if dataset_id is not None:
                await self._require_dataset(session, principal, dataset_id)

            upload_id = new_uuid7()
            staging_key = f"bronze/{principal.org_id}/uploads/{upload_id}"
            s3_upload_id = await self._store.create_multipart(staging_key)
            try:
                upload = Upload(
                    id=upload_id,
                    org_id=principal.org_id,
                    dataset_id=dataset_id,
                    created_by=principal.user_id,
                    filename=name,
                    format=fmt.value,
                    declared_bytes=size_bytes,
                    part_size_bytes=part_size,
                    part_count=part_count,
                    staging_key=staging_key,
                    s3_upload_id=s3_upload_id,
                )
                session.add(upload)
                await session.flush()
                # Only the id: presigned URLs are bearer credentials and are regenerated on
                # replay instead of being stored.
                await idempotency.complete(
                    session,
                    principal,
                    key=idempotency_key,
                    status_code=201,
                    body={"upload_id": str(upload_id)},
                )
                await session.refresh(upload)
            except BaseException:
                await self._best_effort_abort(staging_key, s3_upload_id)
                raise
        return self._ticket(upload)

    # ------------------------------------------------------------------ read / presign

    async def get_upload(self, principal: Principal, upload_id: UUID) -> UploadTicket:
        require(principal, Action.UPLOAD_READ)
        async with self._db.transaction(principal) as session:
            upload = await self._load_upload(session, principal, upload_id)
        return self._ticket(upload)

    async def presign_parts(
        self, principal: Principal, upload_id: UUID, part_numbers: Sequence[int]
    ) -> UploadTicket:
        """Fresh URLs for specific parts: resume after expiry or retry a failed part."""
        require(principal, Action.UPLOAD_CREATE)
        async with self._db.transaction(principal) as session:
            upload = await self._load_upload(session, principal, upload_id)
        if upload.status != "pending":
            raise UploadNotPending(f"This upload is {upload.status}.", hint="Start a new upload.")
        wanted = sorted(set(part_numbers))
        if not wanted or len(wanted) > _MAX_PRESIGN_BATCH:
            raise InvalidParts(f"Ask for between 1 and {_MAX_PRESIGN_BATCH} parts at a time.")
        if wanted[0] < 1 or wanted[-1] > upload.part_count:
            raise InvalidParts(f"Part numbers must be between 1 and {upload.part_count}.")
        return self._ticket(upload, only=wanted)

    # ------------------------------------------------------------------ complete

    async def complete_upload(
        self,
        principal: Principal,
        upload_id: UUID,
        *,
        sha256_hex: str,
        parts: Sequence[CompletedPart],
    ) -> CompletedUpload:
        require(principal, Action.UPLOAD_CREATE)
        if not _SHA256_HEX.fullmatch(sha256_hex):
            raise InvalidParts("The checksum must be 64 hexadecimal characters (SHA-256).")
        digest = bytes.fromhex(sha256_hex)

        outcome = await self._complete(principal, upload_id, digest, parts)
        for key in outcome.cleanup_keys:
            await self._best_effort_delete(key)
        if outcome.abort_multipart is not None:
            await self._best_effort_abort(*outcome.abort_multipart)
        if outcome.error is not None:
            raise outcome.error
        assert outcome.result is not None  # noqa: S101  (an outcome has a result or an error)
        return outcome.result

    async def _complete(
        self, principal: Principal, upload_id: UUID, digest: bytes, parts: Sequence[CompletedPart]
    ) -> _Outcome:
        async with self._db.transaction(principal) as session:
            upload = await self._load_upload(session, principal, upload_id, lock=True)

            if upload.status == "completed":
                return await self._replay_completed(session, principal, upload, digest)
            if upload.status != "pending":
                raise UploadNotPending(
                    f"This upload is {upload.status}.", hint="Start a new upload."
                )
            if self._clock() - upload.created_at > timedelta(
                hours=self._settings.upload_expiry_hours
            ):
                await self._set_status(session, upload, "expired")
                return _Outcome(
                    error=UploadExpired(
                        "This upload was open for too long.", hint="Start a new upload."
                    ),
                    abort_multipart=(
                        (upload.staging_key, upload.s3_upload_id) if upload.s3_upload_id else None
                    ),
                )
            _validate_parts(parts, upload.part_count)

            await self._finish_multipart(upload, parts)
            info = await self._store.head(upload.staging_key)
            if info is None:
                raise UploadNotPending(
                    "The uploaded data could not be found.", hint="Start a new upload."
                )
            if info.size != upload.declared_bytes:
                await self._set_status(session, upload, "aborted")
                return _Outcome(
                    error=UploadSizeMismatch(
                        f"Expected {upload.declared_bytes:,} bytes but received {info.size:,}.",
                        hint="Upload the file again.",
                    ),
                    cleanup_keys=[upload.staging_key],
                )
            head = await self._store.read_range(
                upload.staging_key, 0, min(SNIFF_BYTES, info.size) - 1
            )
            try:
                sniff(head, FileFormat(upload.format))
            except ContentMismatch as exc:
                await self._set_status(session, upload, "aborted")
                return _Outcome(error=exc, cleanup_keys=[upload.staging_key])

            return await self._register_version(session, principal, upload, digest)

    async def _finish_multipart(self, upload: Upload, parts: Sequence[CompletedPart]) -> None:
        if upload.s3_upload_id is None:  # pragma: no cover - set at creation
            raise UploadNotPending("The upload was never started.")
        try:
            await self._store.complete_multipart(upload.staging_key, upload.s3_upload_id, parts)
        except MultipartNotFoundError:
            # A previous attempt completed the multipart upload but crashed before the database
            # commit. The object exists, so carry on and let the size check decide.
            logger.info("multipart already completed", extra={"upload_id": str(upload.id)})
        except InvalidPartsError as exc:
            raise InvalidParts(
                "Storage rejected the list of uploaded parts.",
                hint="Re-upload any missing parts and complete again.",
            ) from exc

    async def _register_version(
        self, session: AsyncSession, principal: Principal, upload: Upload, digest: bytes
    ) -> _Outcome:
        # Serialise completions of identical bytes within an organisation so two racing uploads
        # cannot both create a version for the same content.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{principal.org_id}:{digest.hex()}"},
        )
        existing = await self._find_version_by_hash(session, principal, digest)
        if existing is not None:
            await self._set_status(session, upload, "completed")
            dataset = await self._dataset_info(session, principal, existing.dataset_id)
            return _Outcome(
                result=CompletedUpload(dataset, to_version_info(existing), duplicate=True),
                cleanup_keys=[upload.staging_key],
            )

        if upload.dataset_id is not None:
            dataset_row = await self._require_dataset(session, principal, upload.dataset_id)
        else:
            dataset_row = Dataset(
                id=new_uuid7(),
                org_id=principal.org_id,
                name=dataset_name_from_filename(upload.filename),
                created_by=principal.user_id,
            )
            session.add(dataset_row)
            await session.flush()

        bronze_key = f"bronze/{principal.org_id}/{digest.hex()}"
        # The same file may belong to a deleted dataset whose files wait to be purged: keep
        # it. Cancelling first (the row lock waits for a purge in progress) means the check
        # below sees whether the file is still there.
        await session.execute(
            delete(StoragePurge).where(
                StoragePurge.org_id == principal.org_id, StoragePurge.key == bronze_key
            )
        )
        if await self._store.head(bronze_key) is None:
            await self._store.copy(upload.staging_key, bronze_key)
        version = DatasetVersion(
            id=new_uuid7(),
            org_id=principal.org_id,
            dataset_id=dataset_row.id,
            upload_id=upload.id,
            sha256=digest,
            bytes=upload.declared_bytes,
            format=upload.format,
            bronze_key=bronze_key,
            created_by=principal.user_id,
        )
        session.add(version)
        await session.flush()
        await session.refresh(version)
        await self._set_status(session, upload, "completed")
        dataset = await self._dataset_info(session, principal, dataset_row.id)
        return _Outcome(
            result=CompletedUpload(dataset, to_version_info(version), duplicate=False),
            cleanup_keys=[upload.staging_key],  # deleted only after the transaction committed
        )

    async def _replay_completed(
        self, session: AsyncSession, principal: Principal, upload: Upload, digest: bytes
    ) -> _Outcome:
        version = await self._find_version_by_hash(session, principal, digest)
        if version is None:
            raise UploadNotPending(
                "This upload was completed with a different file.", hint="Start a new upload."
            )
        dataset = await self._dataset_info(session, principal, version.dataset_id)
        return _Outcome(
            result=CompletedUpload(
                dataset, to_version_info(version), duplicate=version.upload_id != upload.id
            )
        )

    # ------------------------------------------------------------------ abort

    async def abort_upload(self, principal: Principal, upload_id: UUID) -> None:
        require(principal, Action.UPLOAD_CREATE)
        async with self._db.transaction(principal) as session:
            upload = await self._load_upload(session, principal, upload_id, lock=True)
            if upload.status == "aborted":
                return
            if upload.status != "pending":
                raise UploadNotPending(f"This upload is already {upload.status}.")
            await self._set_status(session, upload, "aborted")
        if upload.s3_upload_id is not None:
            await self._best_effort_abort(upload.staging_key, upload.s3_upload_id)

    # ------------------------------------------------------------------ helpers

    def _ticket(self, upload: Upload, *, only: Sequence[int] | None = None) -> UploadTicket:
        expires_at = upload.created_at + timedelta(hours=self._settings.upload_expiry_hours)
        targets: tuple[PartTarget, ...] = ()
        if upload.status == "pending" and upload.s3_upload_id is not None:
            numbers = list(only) if only is not None else list(range(1, upload.part_count + 1))
            specs = [PartSpec(n, _part_length(upload, n)) for n in numbers]
            urls = self._store.presign_parts(
                upload.staging_key,
                upload.s3_upload_id,
                specs,
                expires_in=self._settings.upload_url_ttl_seconds,
            )
            targets = tuple(
                PartTarget(s.part_number, urls[s.part_number], s.content_length) for s in specs
            )
        return UploadTicket(
            upload_id=upload.id,
            status=upload.status,
            filename=upload.filename,
            format=upload.format,
            size_bytes=upload.declared_bytes,
            part_size_bytes=upload.part_size_bytes,
            part_count=upload.part_count,
            expires_at=expires_at,
            parts=targets,
        )

    @staticmethod
    async def _load_upload(
        session: AsyncSession, principal: Principal, upload_id: UUID, *, lock: bool = False
    ) -> Upload:
        stmt = select(Upload).where(Upload.id == upload_id, Upload.org_id == principal.org_id)
        if lock:
            stmt = stmt.with_for_update()
        upload = (await session.execute(stmt)).scalar_one_or_none()
        if upload is None:
            raise UploadNotFound("No upload with this id exists in your workspace.")
        return upload

    @staticmethod
    async def _require_dataset(
        session: AsyncSession, principal: Principal, dataset_id: UUID
    ) -> Dataset:
        dataset = (
            await session.execute(
                select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == principal.org_id)
            )
        ).scalar_one_or_none()
        if dataset is None:
            raise DatasetNotFound("No dataset with this id exists in your workspace.")
        return dataset

    @staticmethod
    async def _set_status(session: AsyncSession, upload: Upload, status: str) -> None:
        values: dict[str, Any] = {"status": status}
        if status == "completed":
            values["completed_at"] = func.now()
        await session.execute(update(Upload).where(Upload.id == upload.id).values(**values))
        upload.status = status

    @staticmethod
    async def _find_version_by_hash(
        session: AsyncSession, principal: Principal, digest: bytes
    ) -> DatasetVersion | None:
        return (
            await session.execute(
                select(DatasetVersion)
                .where(DatasetVersion.org_id == principal.org_id, DatasetVersion.sha256 == digest)
                .order_by(DatasetVersion.created_at, DatasetVersion.id)
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _dataset_info(
        session: AsyncSession, principal: Principal, dataset_id: UUID
    ) -> DatasetInfo:
        return await load_dataset_info(session, principal, dataset_id)

    async def _best_effort_abort(self, key: str, s3_upload_id: str) -> None:
        try:
            await self._store.abort_multipart(key, s3_upload_id)
        except Exception:
            logger.warning("could not abort multipart upload", exc_info=True)

    async def _best_effort_delete(self, key: str) -> None:
        try:
            await self._store.delete(key)
        except Exception:
            logger.warning("could not delete staging object", exc_info=True)


# --------------------------------------------------------------------------- pure helpers


def _part_length(upload: Upload, part_number: int) -> int:
    """Exact byte length of a part: full-size parts, then the remainder."""
    if part_number < upload.part_count:
        return upload.part_size_bytes
    return upload.declared_bytes - upload.part_size_bytes * (upload.part_count - 1)


def _validate_parts(parts: Sequence[CompletedPart], part_count: int) -> None:
    numbers = [p.part_number for p in parts]
    if numbers != list(range(1, part_count + 1)):
        raise InvalidParts(
            f"Expected parts 1 to {part_count} in order, each exactly once.",
            hint="Upload every part, then send them sorted by part number.",
        )
    if any(not p.etag.strip('"') for p in parts):
        raise InvalidParts("Every part needs the ETag returned when it was uploaded.")
