"""RFC 9457 ``application/problem+json`` for every failure, including framework errors."""

from __future__ import annotations

import logging
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from insightfulcell.domain.errors import DomainError, ErrorKind
from insightfulcell.logging_config import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"

_STATUS: Final[dict[ErrorKind, int]] = {
    ErrorKind.VALIDATION: 422,
    ErrorKind.UNPROCESSABLE: 422,
    ErrorKind.UNAUTHENTICATED: 401,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.UPSTREAM: 503,
}


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """Problem details (RFC 9457) with two extension members: ``code`` and ``hint``."""

    type: str = Field(description="A URI reference identifying the problem type.")
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str = Field(description="Stable machine-readable code; safe to branch on.")
    hint: str | None = Field(default=None, description="What the user can do next.")
    request_id: str | None = None
    errors: list[FieldError] | None = None


_PROBLEM_SCHEMA: Final = {"schema": {"$ref": "#/components/schemas/Problem"}}


def problem_response() -> dict[str, Any]:
    """OpenAPI response entry: errors are ``application/problem+json`` carrying ``Problem``."""
    return {"model": Problem, "content": {PROBLEM_CONTENT_TYPE: _PROBLEM_SCHEMA}}


PROBLEM_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    code: problem_response() for code in (401, 403, 404, 409, 422, 503)
}


def _problem_response(
    request: Request, problem: Problem, headers: dict[str, str] | None = None
) -> JSONResponse:
    problem.instance = request.url.path
    problem.request_id = request_id_var.get()
    return JSONResponse(
        problem.model_dump(exclude_none=True),
        status_code=problem.status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def _type_uri(code: str) -> str:
    return f"urn:insightfulcell:problem:{code}"


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101  (registered for DomainError only)
    status = _STATUS[exc.kind]
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    problem = Problem(
        type=_type_uri(exc.code),
        title=exc.title,
        status=status,
        detail=exc.detail,
        code=exc.code,
        hint=exc.hint,
    )
    if status >= 500:
        logger.warning("upstream failure", extra={"code": exc.code})
    return _problem_response(request, problem, headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Only the location and the message: pydantic's ``input`` echoes what the caller sent, which
    # can be personal data or a credential.
    errors = [
        FieldError(
            field=".".join(str(part) for part in err["loc"] if part != "body"),
            message=str(err["msg"]),
        )
        for err in exc.errors()
    ]
    problem = Problem(
        type=_type_uri("invalid-request"),
        title="The request is not valid",
        status=422,
        detail="One or more fields are missing or malformed.",
        code="invalid-request",
        errors=errors,
    )
    return _problem_response(request, problem)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    problem = Problem(
        type=_type_uri(f"http-{exc.status_code}"),
        title={404: "Not found", 405: "Method not allowed"}.get(exc.status_code, "Request failed"),
        status=exc.status_code,
        detail=str(exc.detail) if exc.detail else None,
        code=f"http-{exc.status_code}",
    )
    return _problem_response(request, problem, dict(exc.headers) if exc.headers else None)


def internal_error_response(request: Request) -> JSONResponse:
    """The generic 500 body. Never includes exception text: it can contain secrets or SQL."""
    problem = Problem(
        type=_type_uri("internal-error"),
        title="Something went wrong on our side",
        status=500,
        detail="The request could not be completed.",
        code="internal-error",
        hint="Try again. If it keeps happening, contact support with the request id.",
    )
    return _problem_response(request, problem)


def register_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
