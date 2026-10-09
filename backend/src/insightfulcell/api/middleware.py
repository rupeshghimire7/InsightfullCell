"""Pure-ASGI middleware: request id, access log (path only), API security headers and the
last-resort 500 handler.

Unexpected exceptions are caught *here* rather than by Starlette's outermost error middleware,
which sits outside this one and would therefore answer without the request id or security
headers."""

from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from insightfulcell.api.problems import internal_error_response
from insightfulcell.logging_config import request_id_var

logger = logging.getLogger("insightfulcell.access")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")

_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",  # responses can carry presigned URLs and tenant data
    "cross-origin-resource-policy": "same-origin",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get("x-request-id", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                logger.exception("unhandled error", extra={"path": scope["path"]})
                if response_started:
                    raise  # headers already sent: the connection can only be torn down
                await internal_error_response(Request(scope))(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],  # never the query string or headers
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)
