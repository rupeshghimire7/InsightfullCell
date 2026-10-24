"""Deleting runs and datasets (ADR-037).

The rows go at once, in one transaction, through ``app.delete_run`` / ``app.delete_dataset``
(``SECURITY DEFINER``: the append-only logs refuse deletes from the application role). The
files in storage are only scheduled (``storage_purges``) and removed by the worker after
``STORAGE_PURGE_AFTER_DAYS``. A run that is running cannot be deleted: it is cancelled first
and deleted once its worker has stopped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import select, text

from insightfulcell.config import Settings
from insightfulcell.db.models import Upload
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import DatasetNotFound, RunNotFound, StillRunning
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.logging_config import exception_summary
from insightfulcell.queue.port import JobQueue
from insightfulcell.storage.port import ObjectStore

logger = logging.getLogger(__name__)

DELETED: Final = "deleted"
NOT_FOUND: Final = "not_found"
BUSY: Final = "busy"


@dataclass(frozen=True, slots=True)
class Deleted:
    """What a delete removed, and when its files leave storage."""

    run_ids: tuple[UUID, ...]
    files_removed_after: datetime


class DeletionService:
    def __init__(
        self, db: TenantDatabase, store: ObjectStore, queue: JobQueue, settings: Settings
    ) -> None:
        self._db = db
        self._store = store
        self._queue = queue
        self._purge_after = timedelta(days=settings.storage_purge_after_days)

    async def delete_run(self, principal: Principal, run_id: UUID) -> Deleted:
        """Delete a run, its drill-downs and everything they produced."""
        require(principal, Action.RUN_DELETE)
        after = datetime.now(UTC) + self._purge_after
        async with self._db.transaction(principal) as session:
            outcome, run_ids = (
                await session.execute(
                    text("SELECT outcome, run_ids FROM app.delete_run(:target, :after)"),
                    {"target": run_id, "after": after},
                )
            ).one()
        if outcome == NOT_FOUND:
            raise RunNotFound("No run with this id exists in your workspace.")
        if outcome == BUSY:
            raise StillRunning(
                "This analysis (or one of its explorations) is still running.",
                hint="Cancel it, wait until it has stopped, then delete it.",
            )
        await self._discard_jobs(run_ids)
        logger.info("run deleted", extra={"run_id": str(run_id), "runs": len(run_ids)})
        return Deleted(run_ids=tuple(run_ids), files_removed_after=after)

    async def delete_dataset(self, principal: Principal, dataset_id: UUID) -> Deleted:
        """Delete a dataset with its versions, uploads and every analysis of it."""
        require(principal, Action.DATASET_DELETE)
        after = datetime.now(UTC) + self._purge_after
        async with self._db.transaction(principal) as session:
            # Multipart uploads still open for this dataset: their parts are not objects yet,
            # so storage can only drop them when the upload is aborted (after commit).
            open_uploads = (
                await session.execute(
                    select(Upload.staging_key, Upload.s3_upload_id).where(
                        Upload.org_id == principal.org_id,
                        Upload.dataset_id == dataset_id,
                        Upload.status == "pending",
                        Upload.s3_upload_id.is_not(None),
                    )
                )
            ).all()
            outcome, run_ids = (
                await session.execute(
                    text("SELECT outcome, run_ids FROM app.delete_dataset(:target, :after)"),
                    {"target": dataset_id, "after": after},
                )
            ).one()
        if outcome == NOT_FOUND:
            raise DatasetNotFound("No dataset with this id exists in your workspace.")
        if outcome == BUSY:
            raise StillRunning(
                "An analysis of this dataset is still running.",
                hint="Cancel it, wait until it has stopped, then delete the dataset.",
            )
        await self._discard_jobs(run_ids)
        await self._abort_uploads(open_uploads)
        logger.info("dataset deleted", extra={"dataset_id": str(dataset_id), "runs": len(run_ids)})
        return Deleted(run_ids=tuple(run_ids), files_removed_after=after)

    async def delete_workspace(self, principal: Principal) -> Deleted:
        """ "Delete my account" (ADR-043): every dataset, analysis and upload of the workspace,
        its counters and the person's mirror row, at once. The files are due for removal at
        the worker's next sweep: no grace period, since the person asked for everything to go.
        The web app deletes the sign-in account itself afterwards."""
        require(principal, Action.WORKSPACE_DELETE)
        after = datetime.now(UTC)
        async with self._db.transaction(principal) as session:
            open_uploads = (
                await session.execute(
                    select(Upload.staging_key, Upload.s3_upload_id).where(
                        Upload.org_id == principal.org_id,
                        Upload.status == "pending",
                        Upload.s3_upload_id.is_not(None),
                    )
                )
            ).all()
            outcome, run_ids = (
                await session.execute(
                    text("SELECT outcome, run_ids FROM app.delete_workspace(:member, :after)"),
                    {"member": principal.user_id, "after": after},
                )
            ).one()
        if outcome == BUSY:
            raise StillRunning(
                "An analysis in your workspace is still running.",
                hint="Cancel it, wait until it has stopped, then delete your account.",
            )
        await self._discard_jobs(run_ids)
        await self._abort_uploads(open_uploads)
        logger.info("workspace deleted", extra={"runs": len(run_ids)})
        return Deleted(run_ids=tuple(run_ids), files_removed_after=after)

    async def _abort_uploads(self, open_uploads: Sequence[tuple[str, str | None]]) -> None:
        """Multipart uploads still open: their parts are not objects yet, so storage can only
        drop them when the upload is aborted (after commit)."""
        for staging_key, s3_upload_id in open_uploads:
            if s3_upload_id is None:
                continue
            try:
                await self._store.abort_multipart(staging_key, s3_upload_id)
            except Exception as exc:  # the expiry sweep and bucket lifecycle catch it later
                logger.warning(
                    "could not abort an upload of a deleted dataset", extra=exception_summary(exc)
                )

    async def _discard_jobs(self, run_ids: Sequence[UUID]) -> None:
        """Best effort: a waiting job whose run is gone does nothing when a worker takes it."""
        if not run_ids:
            return
        try:
            await self._queue.discard_phase_jobs(run_ids)
        except Exception as exc:
            logger.warning("could not discard jobs of deleted runs", extra=exception_summary(exc))
