"""Structured JSON logging with a per-request id. Tokens, headers and bodies are never logged."""

from __future__ import annotations

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (request_id := request_id_var.get()) is not None:
            payload["request_id"] = request_id
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Uvicorn's own access log would duplicate ours and prints the query string.
    logging.getLogger("uvicorn.access").disabled = True


def exception_summary(exc: BaseException) -> dict[str, str]:
    """An exception's type and stack frames, *without* its message or its causes' messages.

    Parser and driver errors can quote the offending cell value, which may be personal data, so
    worker logs record where something failed, never what the data said."""
    return {
        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "stack": "".join(traceback.format_tb(exc.__traceback__)),
    }
