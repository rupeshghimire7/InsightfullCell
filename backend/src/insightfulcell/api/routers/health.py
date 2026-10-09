"""Liveness and readiness probes (unauthenticated, no tenant data)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from insightfulcell.api.deps import ServicesDep
from insightfulcell.api.problems import problem_response
from insightfulcell.db.revision import EXPECTED_SCHEMA_REVISION
from insightfulcell.logging_config import request_id_var

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness: the process is up")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness: the database is reachable and migrations are current",
    responses={503: problem_response()},
)
async def readyz(services: ServicesDep, response: Response) -> dict[str, str]:
    try:
        async with services.engine.connect() as connection:
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception:
        logger.warning("readiness check could not reach the database", exc_info=True)
        return _not_ready(response, "database-unreachable")
    if revision != EXPECTED_SCHEMA_REVISION:
        return _not_ready(response, "migrations-pending")
    return {"status": "ready", "schema_revision": revision}


def _not_ready(response: Response, reason: str) -> dict[str, str]:
    response.status_code = 503
    return {"status": "not_ready", "reason": reason, "request_id": request_id_var.get() or ""}
