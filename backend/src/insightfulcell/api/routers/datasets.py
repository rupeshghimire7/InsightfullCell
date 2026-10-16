from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from insightfulcell.api.deps import CurrentPrincipal, ServicesDep
from insightfulcell.api.problems import PROBLEM_RESPONSES, problem_response
from insightfulcell.api.schemas import DatasetDetailOut, DatasetPageOut, DeletedOut
from insightfulcell.services.datasets import MAX_PAGE_SIZE

router = APIRouter(prefix="/v1/datasets", tags=["datasets"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=DatasetPageOut, summary="List datasets, newest first")
async def list_datasets(
    principal: CurrentPrincipal,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DatasetPageOut:
    page = await services.datasets.list_datasets(principal, limit=limit, cursor=cursor)
    return DatasetPageOut.model_validate(page)


@router.get("/{dataset_id}", response_model=DatasetDetailOut, summary="A dataset and its versions")
async def get_dataset(
    dataset_id: UUID, principal: CurrentPrincipal, services: ServicesDep
) -> DatasetDetailOut:
    return DatasetDetailOut.model_validate(
        await services.datasets.get_dataset(principal, dataset_id)
    )


@router.delete(
    "/{dataset_id}",
    response_model=DeletedOut,
    summary="Delete a dataset, its versions and every analysis of it",
    responses={409: problem_response()},
)
async def delete_dataset(
    dataset_id: UUID, principal: CurrentPrincipal, services: ServicesDep
) -> DeletedOut:
    """Removes the dataset and everything made from it from the database at once; its files
    leave storage after ``files_removed_after``. ``409`` ``still-running`` while an analysis
    of it is running (cancel it first)."""
    return DeletedOut.model_validate(await services.deletion.delete_dataset(principal, dataset_id))
