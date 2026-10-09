from __future__ import annotations

from fastapi import APIRouter

from insightfulcell.api.deps import CurrentPrincipal, ServicesDep
from insightfulcell.api.problems import PROBLEM_RESPONSES
from insightfulcell.api.schemas import MeOut

router = APIRouter(prefix="/v1", tags=["identity"], responses=PROBLEM_RESPONSES)


@router.get("/me", response_model=MeOut, summary="Who am I, and in which organisation?")
async def me(principal: CurrentPrincipal, services: ServicesDep) -> MeOut:
    """Verifies the token and provisions the user and organisation mirror rows if needed."""
    async with services.db.transaction(principal):
        pass  # opening the transaction is what provisions the mirrors
    return MeOut(
        user_id=principal.user_id,
        org_id=principal.org_id,
        role=principal.role,
        org_name=principal.org_name,
    )
