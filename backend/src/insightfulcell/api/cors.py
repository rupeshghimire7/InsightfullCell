"""CORS for the one endpoint browsers call on the API directly: the run event stream.

Every other endpoint is reached only through the web app's server (the BFF), so no other path
answers cross-origin requests. The stream is authorised by a run-scoped ticket in the
``Authorization`` header, not by cookies, so credentials mode stays off.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

EVENTS_PATH: Final = re.compile(r"^/v1/runs/[0-9a-fA-F-]{36}/events$")


class EventStreamCorsMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.cors = CORSMiddleware(
            app,
            allow_origins=list(allowed_origins),
            allow_methods=["GET"],
            allow_headers=["Authorization", "Last-Event-ID", "Accept"],
            expose_headers=["X-Request-Id"],
            allow_credentials=False,
            max_age=600,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and EVENTS_PATH.fullmatch(scope["path"]):
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
