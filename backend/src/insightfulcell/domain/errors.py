"""Domain error catalogue.

Every error a caller can cause or recover from is a ``DomainError`` with a stable ``code``.
The API layer maps ``kind`` to an HTTP status and renders RFC 9457 ``problem+json``; the domain
itself knows nothing about HTTP. Messages say what happened *and what to do next*.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar


class ErrorKind(StrEnum):
    """Coarse failure category; the API layer owns the kind -> HTTP status mapping."""

    VALIDATION = "validation"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PRECONDITION_FAILED = "precondition_failed"
    UNPROCESSABLE = "unprocessable"
    UPSTREAM = "upstream"


class DomainError(Exception):
    """Base class. Subclasses fix ``kind``, ``code`` and ``title``."""

    kind: ClassVar[ErrorKind] = ErrorKind.VALIDATION
    code: ClassVar[str] = "domain-error"
    title: ClassVar[str] = "Request could not be processed"

    def __init__(
        self,
        detail: str,
        *,
        hint: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.hint = hint
        self.extra: dict[str, Any] = dict(extra or {})


# --------------------------------------------------------------------------- identity


class Unauthenticated(DomainError):
    kind = ErrorKind.UNAUTHENTICATED
    code = "unauthenticated"
    title = "Authentication required"


class IdentityProviderUnavailable(DomainError):
    kind = ErrorKind.UPSTREAM
    code = "identity-provider-unavailable"
    title = "Sign-in service is unavailable"


class DatabaseUnavailable(DomainError):
    kind = ErrorKind.UPSTREAM
    code = "database-unavailable"
    title = "The database is unavailable"


class Forbidden(DomainError):
    kind = ErrorKind.FORBIDDEN
    code = "forbidden"
    title = "You do not have permission to do this"


# --------------------------------------------------------------------------- uploads


class InvalidFilename(DomainError):
    code = "invalid-filename"
    title = "Invalid file name"


class UnsupportedFileType(DomainError):
    code = "unsupported-file-type"
    title = "Unsupported file type"


class FileTooLarge(DomainError):
    code = "file-too-large"
    title = "File is too large"


class ContentMismatch(DomainError):
    code = "content-mismatch"
    title = "File content does not match its type"


class UploadSizeMismatch(DomainError):
    code = "upload-size-mismatch"
    title = "Uploaded size differs from the declared size"


class InvalidParts(DomainError):
    code = "invalid-parts"
    title = "Upload parts are invalid"


class InvalidCursor(DomainError):
    code = "invalid-cursor"
    title = "Invalid pagination cursor"


class UploadNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    code = "upload-not-found"
    title = "Upload not found"


class DatasetNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    code = "dataset-not-found"
    title = "Dataset not found"


class UploadNotPending(DomainError):
    kind = ErrorKind.CONFLICT
    code = "upload-not-pending"
    title = "Upload is no longer open"


class UploadExpired(DomainError):
    kind = ErrorKind.CONFLICT
    code = "upload-expired"
    title = "Upload expired"


class IdempotencyInProgress(DomainError):
    kind = ErrorKind.CONFLICT
    code = "idempotency-in-progress"
    title = "A request with this Idempotency-Key is still being processed"


class IdempotencyKeyReused(DomainError):
    kind = ErrorKind.UNPROCESSABLE
    code = "idempotency-key-reused"
    title = "Idempotency-Key was used with a different request"


class StorageUnavailable(DomainError):
    kind = ErrorKind.UPSTREAM
    code = "storage-unavailable"
    title = "Object storage is unavailable"


# --------------------------------------------------------------------------- runs


class RunNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    code = "run-not-found"
    title = "Run not found"


class DatasetVersionNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    code = "dataset-version-not-found"
    title = "Dataset version not found"


class DatasetVersionPurged(DomainError):
    kind = ErrorKind.CONFLICT
    code = "dataset-version-purged"
    title = "The original file is no longer stored"


class RunNotInState(DomainError):
    """A transition was asked of a run whose status does not allow it."""

    kind = ErrorKind.CONFLICT
    code = "run-state-conflict"
    title = "The run is not in a state that allows this"


class StillRunning(DomainError):
    """A run (or a run of the dataset) is running, so it cannot be deleted yet."""

    kind = ErrorKind.CONFLICT
    code = "still-running"
    title = "An analysis is still running"


class RunChanged(DomainError):
    """``If-Match`` named a version of the run that is no longer current."""

    kind = ErrorKind.PRECONDITION_FAILED
    code = "run-changed"
    title = "The run changed since you last loaded it"


# --------------------------------------------------------------------------- plans


class PlanNotReady(DomainError):
    """The run has not produced its analysis plan yet."""

    kind = ErrorKind.CONFLICT
    code = "plan-not-ready"
    title = "The analysis plan is not ready yet"


class ReportNotReady(DomainError):
    """The run has not finished writing its report yet."""

    kind = ErrorKind.CONFLICT
    code = "report-not-ready"
    title = "The report is not ready yet"


class NarrationNotRetryable(DomainError):
    """The report's narration was not simplified by a model outage, so writing it again would
    change nothing."""

    kind = ErrorKind.CONFLICT
    code = "narration-not-retryable"
    title = "This report cannot be narrated again"


class ReportOutdated(DomainError):
    """The run was made by an earlier version of the analysis, so a step that re-runs part of
    it (narrating again, exploring a subset) cannot reuse its results."""

    kind = ErrorKind.CONFLICT
    code = "report-outdated"
    title = "This report was made by an earlier version of the analysis"


class DataNotAvailable(DomainError):
    """The run has not read its file yet (or reading it failed), so there are no rows to show."""

    kind = ErrorKind.CONFLICT
    code = "data-not-available"
    title = "The data is not available yet"


class DashboardNotAvailable(DomainError):
    """The report exists, but was written before dashboards were assembled."""

    kind = ErrorKind.CONFLICT
    code = "dashboard-not-available"
    title = "This report has no dashboard"


class DrilldownNotAllowed(DomainError):
    """Exploring a subset needs a finished full analysis."""

    kind = ErrorKind.CONFLICT
    code = "drilldown-not-allowed"
    title = "This analysis cannot be explored further"


class InvalidDrilldown(DomainError):
    code = "invalid-drilldown"
    title = "This subset cannot be explored"


class SubsetTooSmall(DomainError):
    """Raised by the worker when a drill-down's filter leaves too few rows; safe to show."""

    kind = ErrorKind.UNPROCESSABLE
    code = "subset-too-small"
    title = "Too few rows to explore"


class CleaningPlanChanged(DomainError):
    """The decision names a cleaning plan revision that is no longer the latest."""

    kind = ErrorKind.PRECONDITION_FAILED
    code = "cleaning-plan-changed"
    title = "The cleaning plan changed since you loaded it"


class InvalidDecision(DomainError):
    code = "invalid-decision"
    title = "The decision is not valid"


# --------------------------------------------------------------------------- ingestion


class IngestionError(DomainError):
    """Raised by the ingestion engine; always safe to show the user."""

    code = "ingestion-error"
    title = "File could not be ingested"


class TooManyColumns(IngestionError):
    code = "too-many-columns"
    title = "File has too many columns"


class TooManyRows(IngestionError):
    code = "too-many-rows"
    title = "File has too many rows"


class ParseFailure(IngestionError):
    code = "parse-failure"
    title = "File could not be parsed"


class ParseTimeout(IngestionError):
    code = "parse-timeout"
    title = "Parsing took too long"


class ArchiveBomb(IngestionError):
    code = "archive-bomb"
    title = "Spreadsheet archive is unsafe"


class IntegrityMismatch(IngestionError):
    code = "integrity-mismatch"
    title = "File content does not match its recorded checksum"
