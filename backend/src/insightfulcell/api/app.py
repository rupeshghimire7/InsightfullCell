"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from insightfulcell import __version__
from insightfulcell.api.container import Services, build_services
from insightfulcell.api.cors import EventStreamCorsMiddleware
from insightfulcell.api.middleware import RequestContextMiddleware
from insightfulcell.api.problems import register_problem_handlers
from insightfulcell.api.routers import datasets, health, me, runs, uploads
from insightfulcell.config import Settings, get_settings
from insightfulcell.logging_config import configure_logging


def create_app(settings: Settings | None = None, *, services: Services | None = None) -> FastAPI:
    """Build the app. Tests inject ``services`` (fake storage, test engine); production lets the
    lifespan build real adapters from ``settings``."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = services or build_services(settings)
        app.state.services = container
        await container.start()
        try:
            yield
        finally:
            await container.stop()

    is_prod = settings.environment == "production"
    app = FastAPI(
        title="InsightfulCell API",
        version=__version__,
        description="Identity-verified, tenant-isolated uploads, datasets and analysis runs.",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    # Added last, so it is outermost: preflight requests are answered before anything else.
    app.add_middleware(EventStreamCorsMiddleware, allowed_origins=settings.events_origins)
    register_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(uploads.router)
    app.include_router(datasets.router)
    app.include_router(runs.router)
    app.include_router(runs.events_router)
    return app
