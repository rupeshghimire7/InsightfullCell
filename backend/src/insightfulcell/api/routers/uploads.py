"""Direct-to-storage multipart uploads (IC-006, FR-INGEST-01, FR-INGEST-06).

Flow: ``POST /v1/uploads`` -> browser PUTs parts straight to object storage -> ``:complete``.
The API never receives file bytes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from insightfulcell.api.deps import CurrentPrincipal, IdempotencyKey, ServicesDep
from insightfulcell.api.problems import PROBLEM_RESPONSES
from insightfulcell.api.schemas import (
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateUploadRequest,
    PresignPartsRequest,
    UploadOut,
)
from insightfulcell.storage.port import CompletedPart

router = APIRouter(prefix="/v1/uploads", tags=["uploads"], responses=PROBLEM_RESPONSES)


@router.post(
    "",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start an upload and get presigned part URLs",
)
async def create_upload(
    body: CreateUploadRequest,
    idempotency_key: IdempotencyKey,
    principal: CurrentPrincipal,
    services: ServicesDep,
) -> UploadOut:
    """Validates the name, type and size, then returns one presigned URL per part. Replaying
    the same request with the same ``Idempotency-Key`` returns the same upload with fresh URLs."""
    ticket = await services.uploads.create_upload(
        principal,
        filename=body.filename,
        size_bytes=body.size_bytes,
        dataset_id=body.dataset_id,
        idempotency_key=idempotency_key,
    )
    return UploadOut.model_validate(ticket)


@router.get("/{upload_id}", response_model=UploadOut, summary="Upload status")
async def get_upload(
    upload_id: UUID, principal: CurrentPrincipal, services: ServicesDep
) -> UploadOut:
    return UploadOut.model_validate(await services.uploads.get_upload(principal, upload_id))


@router.post(
    "/{upload_id}:presign-parts",
    response_model=UploadOut,
    summary="Fresh URLs for specific parts (resume or retry)",
)
async def presign_parts(
    upload_id: UUID,
    body: PresignPartsRequest,
    principal: CurrentPrincipal,
    services: ServicesDep,
) -> UploadOut:
    ticket = await services.uploads.presign_parts(principal, upload_id, body.part_numbers)
    return UploadOut.model_validate(ticket)


@router.post(
    "/{upload_id}:complete",
    response_model=CompleteUploadResponse,
    summary="Finish an upload and create the dataset version",
)
async def complete_upload(
    upload_id: UUID,
    body: CompleteUploadRequest,
    principal: CurrentPrincipal,
    services: ServicesDep,
) -> CompleteUploadResponse:
    """Verifies the part list, total size and file signature, then deduplicates by SHA-256:
    identical bytes in your organisation return the existing version (``duplicate: true``)."""
    result = await services.uploads.complete_upload(
        principal,
        upload_id,
        sha256_hex=body.sha256,
        parts=[CompletedPart(p.part_number, p.etag) for p in body.parts],
    )
    return CompleteUploadResponse.model_validate(result)


@router.post(
    "/{upload_id}:abort",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel an upload and discard uploaded parts",
)
async def abort_upload(
    upload_id: UUID, principal: CurrentPrincipal, services: ServicesDep
) -> Response:
    await services.uploads.abort_upload(principal, upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
