"""Builds the worker's object graph and runs it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType

import procrastinate
from sqlalchemy.ext.asyncio import AsyncEngine

from insightfulcell.config import Settings
from insightfulcell.db.engine import create_engine
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import Queue
from insightfulcell.engine.ports import LanguageModel
from insightfulcell.llm.breaker import CircuitBreaker
from insightfulcell.llm.fallback import FallbackModel
from insightfulcell.llm.gateway import GatewayConfig, LLMGateway
from insightfulcell.llm.providers import (
    build_fallback_transport,
    build_transport,
    fallback_models,
    models_for,
)
from insightfulcell.llm.transport import (
    PermanentProviderError,
    TransientProviderError,
    Transport,
)
from insightfulcell.pipeline.definition import Pipeline, default_pipeline
from insightfulcell.queue.procrastinate_queue import ProcrastinateQueue, create_procrastinate_app
from insightfulcell.services.llm_ledger import DbUsageLedger
from insightfulcell.services.llm_spend import DbSpendGuard
from insightfulcell.services.maintenance import Maintenance
from insightfulcell.services.run_executor import RunExecutor
from insightfulcell.storage.port import ObjectStore
from insightfulcell.storage.s3 import S3ObjectStore
from insightfulcell.worker.tasks import register_tasks

logger = logging.getLogger(__name__)

ALL_QUEUES: tuple[str, ...] = tuple(queue.value for queue in Queue)
HEARTBEAT_SECONDS = 10.0


@dataclass(slots=True)
class WorkerRuntime:
    settings: Settings
    engine: AsyncEngine
    app: procrastinate.App
    queue: ProcrastinateQueue
    executor: RunExecutor
    maintenance: Maintenance
    owns_engine: bool = True
    model: LanguageModel | None = None

    async def __aenter__(self) -> WorkerRuntime:
        await self.queue.open()
        # Make sure this month's (and the next months') event partitions exist before any run
        # writes events, even if the daily task has not run yet.
        created = await self.maintenance.ensure_event_partitions()
        if created:
            logger.info("created run event partitions", extra={"count": created})
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.queue.close()
        if self.owns_engine:
            await self.engine.dispose()

    async def run(
        self,
        *,
        queues: Sequence[str] = ALL_QUEUES,
        concurrency: int | None = None,
        name: str | None = None,
        wait: bool = True,
        install_signal_handlers: bool = True,
    ) -> None:
        await self.app.run_worker_async(
            queues=list(queues),
            concurrency=concurrency or self.settings.worker_concurrency,
            name=name or "insightfulcell-worker",
            wait=wait,
            install_signal_handlers=install_signal_handlers,
            update_heartbeat_interval=HEARTBEAT_SECONDS,
            stalled_worker_timeout=float(self.settings.run_stalled_after_seconds),
            delete_jobs="successful",
        )


def build_gateway(settings: Settings, db: TenantDatabase) -> FallbackModel:
    """The configured model, its backup (``LLM_FALLBACK_PROVIDER``) and the spend cap they
    share. Off when neither has an API key: every run then plans with rules."""
    spend = DbSpendGuard(
        db,
        cap_usd=settings.llm_spend_cap_usd,
        user_cap_usd=settings.llm_user_spend_cap_usd,
        label=settings.llm_usage_label,
        hold_seconds=settings.llm_spend_hold_seconds,
        usd_to_aud=settings.llm_usd_to_aud,
        provider_caps_usd=settings.provider_spend_caps_usd,
    )
    primary = _gateway(settings, db, spend, build_transport(settings), models_for(settings))
    backup_transport = build_fallback_transport(settings)
    backup = (
        _gateway(settings, db, spend, backup_transport, fallback_models(settings), role="backup")
        if backup_transport is not None
        else None
    )
    model = FallbackModel(primary, backup)
    if model.identity is None:
        logger.info(
            "language model off: runs plan with rules", extra={"provider": settings.llm_provider}
        )
    else:
        logger.info(
            "language model spend cap",
            extra={
                "cap_aud": _cap(settings.llm_spend_cap_aud),
                "provider_caps_aud": {
                    p: _cap(cap) for p, cap in settings.provider_spend_caps_aud.items()
                },
                "user_cap_aud": str(settings.llm_user_spend_cap_aud),
                "usd_to_aud": str(settings.llm_usd_to_aud),
                "label": settings.llm_usage_label,
                "backup": backup.identity if backup else None,
            },
        )
    return model


def _cap(aud: Decimal | None) -> str:
    return "none" if aud is None else str(aud)


def _gateway(  # noqa: PLR0913  (one gateway per provider, same settings)
    settings: Settings,
    db: TenantDatabase,
    spend: DbSpendGuard,
    transport: Transport | None,
    models: dict[str, str],
    *,
    role: str = "primary",
) -> LLMGateway:
    return LLMGateway(
        transport,
        DbUsageLedger(db),
        GatewayConfig(
            models=models,
            max_attempts=settings.llm_max_attempts,
            run_input_token_budget=settings.llm_run_input_token_budget,
            max_output_tokens=settings.llm_max_output_tokens,
            prices=settings.llm_prices,
            requests_per_minute=settings.llm_requests_per_minute,
            max_rate_limit_waits=settings.llm_rate_limit_waits,
            max_retry_wait_seconds=settings.llm_max_retry_wait_seconds,
            role=role,
        ),
        CircuitBreaker(
            threshold=settings.llm_breaker_failures,
            open_seconds=settings.llm_breaker_open_seconds,
        ),
        spend=spend,
    )


class ModelNotUsable(RuntimeError):
    """The configured language model cannot work: the worker must not start pretending."""


async def check_model(model: LanguageModel | None, settings: Settings) -> None:
    """Before taking work, prove the configured keys and models work (a free lookup, no tokens).

    With one model, a rejected key or an unknown model stops the worker with a message saying
    what to fix. With a backup, the worker starts as long as one of the two works (the other is
    logged as an error and skipped by its breaker). A provider that cannot be reached right now,
    or a quota used up for today, only warns (runs then fall back until it is back).
    """
    if isinstance(model, LLMGateway):
        model = FallbackModel(model, None)
    if not isinstance(model, FallbackModel) or model.identity is None:
        return
    candidates = [
        (gateway, provider, setting)
        for gateway, provider, setting in (
            (model.primary, settings.llm_provider, "LLM_MODEL_STRONG and LLM_MODEL_FAST"),
            (model.backup, settings.llm_fallback_provider, "LLM_FALLBACK_MODEL"),
        )
        if gateway is not None and gateway.identity is not None
    ]
    problems = [
        problem
        for gateway, provider, setting in candidates
        if (problem := await _check_one(gateway, provider, setting)) is not None
    ]
    if problems and len(problems) == len(candidates):
        raise ModelNotUsable(" ".join(problems))
    for problem in problems:
        logger.error("language model not usable: runs use the other one", extra={"why": problem})


async def _check_one(gateway: LLMGateway, provider: str, setting: str) -> str | None:
    """Why this gateway's key or models cannot work, or ``None`` when they can (or might)."""
    identity = gateway.identity
    models = sorted(set(gateway.models.values()))
    try:
        await gateway.check()
    except PermanentProviderError as exc:
        if exc.reason == "quota_exhausted":
            logger.warning("language model quota used up at start-up", extra={"provider": provider})
            return None
        key = f"{provider.upper()}_API_KEY"
        if exc.reason == "auth_failed":
            return f"{provider} rejected {key}. Check the key, or set LLM_PROVIDER=none."
        if exc.reason == "model_not_found":
            return (
                f"{provider} has no model {' or '.join(repr(m) for m in models)} for this key. "
                f"Set {setting} to models the key can use."
            )
        return f"{provider} refused the start-up check ({exc.reason})."
    except TransientProviderError:
        logger.warning(
            "could not reach the language model at start-up; runs will try it",
            extra={"provider": provider},
        )
        return None
    logger.info("language model ready", extra={"provider": provider, "model": identity})
    return None


def build_worker(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    store: ObjectStore | None = None,
    pipeline: Pipeline | None = None,
    model: LanguageModel | None = None,
) -> WorkerRuntime:
    owns_engine = engine is None
    engine = engine or create_engine(settings)
    db = TenantDatabase(engine)
    store = store or S3ObjectStore(settings)
    app = create_procrastinate_app(settings, max_pool_size=settings.worker_concurrency + 4)
    queue = ProcrastinateQueue(app)
    model = model or build_gateway(settings, db)
    pipeline = pipeline or default_pipeline(
        card_token_budget=settings.schema_card_token_budget,
        model_identity=model.identity,
        judge=settings.llm_judge,
        narration_concurrency=settings.llm_narration_concurrency,
    )
    executor = RunExecutor(db, store, queue, pipeline, settings, model=model)
    maintenance = Maintenance(db, store, queue, executor, settings)
    register_tasks(app, executor, maintenance)
    return WorkerRuntime(
        settings=settings,
        engine=engine,
        app=app,
        queue=queue,
        executor=executor,
        maintenance=maintenance,
        owns_engine=owns_engine,
        model=model,
    )
