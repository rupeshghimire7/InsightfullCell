from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from insightfulcell.api.deps import CurrentPrincipal, ServicesDep
from insightfulcell.api.problems import PROBLEM_RESPONSES
from insightfulcell.api.schemas import DatasetDetailOut, DatasetPageOut
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
