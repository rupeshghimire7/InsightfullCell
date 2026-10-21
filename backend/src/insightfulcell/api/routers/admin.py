"""The admin dashboard's data (the web app's /dashboard), for the web server only.

Not in the public API schema, not forwarded by the web app's proxy (its allow-list has no
``v1/admin``), and not published by Caddy: only the web server reaches it, over the private
network, with ``X-Admin-Key``. A missing or wrong key and a switched-off endpoint get the same
404, so a caller learns nothing about which.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query

from insightfulcell.api.deps import ServicesDep
from insightfulcell.domain.errors import AdminNotFound
from insightfulcell.services.admin import admin_key_matches

router = APIRouter(prefix="/v1/admin", include_in_schema=False)


async def require_admin_key(
    services: ServicesDep,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key", max_length=512)] = None,
) -> None:
    if not admin_key_matches(x_admin_key, services.settings.admin_dashboard_key):
        raise AdminNotFound("Not found.")


@router.get("/overview", dependencies=[Depends(require_admin_key)])
async def overview(
    services: ServicesDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> dict[str, Any]:
    return await services.admin.overview(days)
