"""FastAPI dependencies: the authenticated principal and the service container."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from insightfulcell.api.container import Services
from insightfulcell.domain.errors import Unauthenticated
from insightfulcell.domain.security import Principal

_bearer = HTTPBearer(auto_error=False, description="Short-lived JWT issued by the web app.")


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


async def get_principal(
    services: Annotated[Services, Depends(get_services)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise Unauthenticated(
            "Send the access token as 'Authorization: Bearer <token>'.",
            hint="Sign in to get a session.",
        )
    return await services.verifier.verify(credentials.credentials)


ServicesDep = Annotated[Services, Depends(get_services)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Client-generated unique key; makes retries of this request safe.",
    ),
]
