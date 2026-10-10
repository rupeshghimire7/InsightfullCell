"""Composition root: builds the object graph once at start-up (dependency injection by hand)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.auth.tokens import JwksKeyResolver, SigningKeyResolver, TokenVerifier
from insightfulcell.config import Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.db.notifications import EventBroker
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.pipeline.definition import Pipeline, default_pipeline
from insightfulcell.queue.port import JobQueue
from insightfulcell.queue.procrastinate_queue import ProcrastinateQueue, create_procrastinate_app
from insightfulcell.services.datasets import DatasetService
from insightfulcell.services.plans import PlanService
from insightfulcell.services.reports import ReportService
from insightfulcell.services.runs import RunService
from insightfulcell.services.uploads import UploadService
from insightfulcell.storage.port import ObjectStore
from insightfulcell.storage.s3 import S3ObjectStore


@dataclass(slots=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    db: TenantDatabase
    verifier: TokenVerifier
    ticket_verifier: TokenVerifier
    uploads: UploadService
    datasets: DatasetService
    runs: RunService
    plans: PlanService
    reports: ReportService
    queue: JobQueue
    broker: EventBroker
    owns_engine: bool = True
    owns_queue: bool = True

    async def start(self) -> None:
        if self.owns_queue and isinstance(self.queue, ProcrastinateQueue):
            await self.queue.open()
        await self.broker.start()

    async def stop(self) -> None:
        await self.broker.stop()
        if self.owns_queue and isinstance(self.queue, ProcrastinateQueue):
            await self.queue.close()
        if self.owns_engine:
            await self.engine.dispose()


def build_services(  # noqa: PLR0913  (keyword-only overrides for tests)
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    store: ObjectStore | None = None,
    verifier: TokenVerifier | None = None,
    ticket_verifier: TokenVerifier | None = None,
    queue: JobQueue | None = None,
    broker: EventBroker | None = None,
    pipeline: Pipeline | None = None,
) -> Services:
    """Wire production adapters; tests pass fakes or a test engine for any of them."""
    owns_engine = engine is None
    owns_queue = queue is None
    engine = engine or create_engine(settings)
    db = TenantDatabase(engine)
    store = store or S3ObjectStore(settings)
    resolver: SigningKeyResolver = JwksKeyResolver(settings.auth_jwks_url)
    verifier = verifier or TokenVerifier(
        resolver,
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        leeway_seconds=settings.auth_clock_skew_seconds,
    )
    ticket_verifier = ticket_verifier or TokenVerifier(
        resolver,
        issuer=settings.auth_issuer,
        audience=settings.auth_events_audience,
        leeway_seconds=settings.auth_clock_skew_seconds,
    )
    queue = queue or ProcrastinateQueue(create_procrastinate_app(settings))
    broker = broker or EventBroker(settings.psycopg_conninfo if settings.events_listen else None)
    return Services(
        settings=settings,
        engine=engine,
        db=db,
        verifier=verifier,
        ticket_verifier=ticket_verifier,
        uploads=UploadService(db, store, settings),
        datasets=DatasetService(db),
        runs=RunService(db, queue, pipeline or default_pipeline()),
        plans=PlanService(db, queue),
        reports=ReportService(db),
        queue=queue,
        broker=broker,
        owns_engine=owns_engine,
        owns_queue=owns_queue,
    )
