"""ASGI entry point: ``uvicorn insightfulcell.api.main:app``."""

from __future__ import annotations

from insightfulcell.api.app import create_app

app = create_app()
