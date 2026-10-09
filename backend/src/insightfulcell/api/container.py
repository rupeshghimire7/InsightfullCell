"""Composition root: builds the object graph once at start-up (dependency injection by hand)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.auth.tokens import JwksKeyResolver, TokenVerifier
from insightfulcell.config import Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.services.datasets import DatasetService
from insightfulcell.services.uploads import UploadService
from insightfulcell.storage.port import ObjectStore
from insightfulcell.storage.s3 import S3ObjectStore


@dataclass(slots=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    db: TenantDatabase
    verifier: TokenVerifier
    uploads: UploadService
    datasets: DatasetService
    owns_engine: bool = True


def build_services(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    store: ObjectStore | None = None,
    verifier: TokenVerifier | None = None,
) -> Services:
    """Wire production adapters; tests pass fakes for ``store``/``verifier`` or a test engine."""
    owns_engine = engine is None
    engine = engine or create_engine(settings)
    db = TenantDatabase(engine)
    store = store or S3ObjectStore(settings)
    verifier = verifier or TokenVerifier(
        JwksKeyResolver(settings.auth_jwks_url),
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        leeway_seconds=settings.auth_clock_skew_seconds,
    )
    return Services(
        settings=settings,
        engine=engine,
        db=db,
        verifier=verifier,
        uploads=UploadService(db, store, settings),
        datasets=DatasetService(db),
        owns_engine=owns_engine,
    )
