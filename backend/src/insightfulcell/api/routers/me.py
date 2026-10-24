from __future__ import annotations

from fastapi import APIRouter

from insightfulcell.api.deps import CurrentPrincipal, ServicesDep
from insightfulcell.api.problems import PROBLEM_RESPONSES, problem_response
from insightfulcell.api.schemas import DeletedOut, MeOut

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


@router.delete(
    "/me",
    response_model=DeletedOut,
    summary='Delete everything in my workspace ("Delete my account")',
    responses={409: problem_response()},
)
async def delete_me(principal: CurrentPrincipal, services: ServicesDep) -> DeletedOut:
    """Removes every dataset, analysis, upload and usage counter of the caller's workspace from
    the database at once (owner only); the files leave storage at the worker's next sweep
    (``files_removed_after`` is now). ``409`` ``still-running`` while an analysis is running.
    The web app then deletes the sign-in account (ADR-043)."""
    return DeletedOut.model_validate(await services.deletion.delete_workspace(principal))
