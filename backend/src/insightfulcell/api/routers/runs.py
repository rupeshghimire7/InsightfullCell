"""Runs (IC-007) and their live progress stream (IC-008).

``POST /v1/runs`` commits a queued run and answers ``202`` with the run and its events URL; the
analysis happens in the worker. Progress is a Server-Sent Events stream the browser opens on the
API directly, authorised by a 5-minute ticket for that one run (minted by the web app), so no
web function is held open for the length of an analysis.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from insightfulcell.api.container import Services
from insightfulcell.api.deps import CurrentPrincipal, IdempotencyKey, ServicesDep
from insightfulcell.api.problems import (
    PROBLEM_ONLY_RESPONSES,
    PROBLEM_RESPONSES,
    problem_response,
)
from insightfulcell.api.schemas import (
    CleaningOperationOut,
    CleaningPlanOut,
    CreateRunRequest,
    DecideCleaningRequest,
    PlanColumnOut,
    PlanOut,
    ReportOut,
    RevisePlanRequest,
    RunEventOut,
    RunOut,
    RunPageOut,
    RunSummaryOut,
    StageOut,
)
from insightfulcell.auth.tokens import EventTicket
from insightfulcell.domain.errors import DomainError, RunChanged, RunNotFound, Unauthenticated
from insightfulcell.domain.runs import RunStatus
from insightfulcell.engine.cleaning.operations import CATALOGUE
from insightfulcell.engine.planning.language import CleaningKind
from insightfulcell.services.datasets import MAX_PAGE_SIZE
from insightfulcell.services.dto import RunDetail, RunEventInfo
from insightfulcell.services.plans import ColumnEdit, PlanDecision, PlanView
from insightfulcell.services.reports import ReportView
from insightfulcell.services.run_store import EVENT_BATCH

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/runs", tags=["runs"], responses=PROBLEM_RESPONSES)
events_router = APIRouter(prefix="/v1/runs", tags=["runs"], responses=PROBLEM_ONLY_RESPONSES)

#: Browsers reconnect after this long when a stream drops (the ``retry:`` field).
RECONNECT_MS: Final = 3_000
_ticket_bearer = HTTPBearer(
    auto_error=False, description="Run-scoped event ticket (5 minutes, audience events)."
)
IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=64,
        description="The run's row_version (ETag) you last saw; 412 if it changed since.",
    ),
]


def events_url(run_id: UUID) -> str:
    return f"/v1/runs/{run_id}/events"


def to_run_out(detail: RunDetail) -> RunOut:
    summary = RunSummaryOut.model_validate(detail.run)
    return RunOut(
        **summary.model_dump(),
        stages=[StageOut.model_validate(stage) for stage in detail.stages],
        events_url=events_url(detail.run.id),
    )


def etag(row_version: int) -> str:
    return f'"{row_version}"'


def parse_if_match(value: str | None) -> int | None:
    """``"7"``, ``W/"7"`` or ``7`` -> 7. Anything else can never match: 412."""
    if value is None:
        return None
    raw = value.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise RunChanged(
            "The If-Match header does not name a version of this run.",
            hint="Send the run's row_version, as returned when you loaded it.",
        )
    return int(raw)


# --------------------------------------------------------------------------- resources


@router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an analysis of a dataset",
)
async def create_run(
    body: CreateRunRequest,
    idempotency_key: IdempotencyKey,
    principal: CurrentPrincipal,
    services: ServicesDep,
    response: Response,
) -> RunOut:
    """Queues a run of the dataset's newest version (or ``dataset_version_id``) and returns at
    once; follow ``events_url`` for progress. Replaying with the same ``Idempotency-Key`` returns
    the same run instead of starting another."""
    accepted = await services.runs.create_run(
        principal,
        dataset_id=body.dataset_id,
        dataset_version_id=body.dataset_version_id,
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/v1/runs/{accepted.detail.run.id}"
    response.headers["ETag"] = etag(accepted.detail.run.row_version)
    return to_run_out(accepted.detail)


@router.get("", response_model=RunPageOut, summary="List runs, newest first")
async def list_runs(
    principal: CurrentPrincipal,
    services: ServicesDep,
    dataset_id: Annotated[UUID | None, Query(description="Only runs of this dataset.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> RunPageOut:
    page = await services.runs.list_runs(
        principal, dataset_id=dataset_id, limit=limit, cursor=cursor
    )
    return RunPageOut.model_validate(page)


@router.get("/{run_id}", response_model=RunOut, summary="A run, its stages and their results")
async def get_run(
    run_id: UUID, principal: CurrentPrincipal, services: ServicesDep, response: Response
) -> RunOut:
    detail = await services.runs.get_run(principal, run_id)
    response.headers["ETag"] = etag(detail.run.row_version)
    return to_run_out(detail)


@router.post(
    "/{run_id}:cancel",
    response_model=RunOut,
    summary="Cancel a queued, running or review-waiting run",
    responses={412: problem_response()},
)
async def cancel_run(
    run_id: UUID,
    principal: CurrentPrincipal,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> RunOut:
    """A running run stops at the next stage boundary. Safe to repeat."""
    detail = await services.runs.cancel_run(principal, run_id, if_match=parse_if_match(if_match))
    response.headers["ETag"] = etag(detail.run.row_version)
    return to_run_out(detail)


@router.post(
    "/{run_id}:retry",
    response_model=RunOut,
    summary="Queue a failed run again; finished stages are not redone",
    responses={412: problem_response()},
)
async def retry_run(
    run_id: UUID,
    principal: CurrentPrincipal,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> RunOut:
    detail = await services.runs.retry_run(principal, run_id, if_match=parse_if_match(if_match))
    response.headers["ETag"] = etag(detail.run.row_version)
    return to_run_out(detail)


# --------------------------------------------------------------------------- plans


def to_plan_out(view: PlanView) -> PlanOut:
    plan = view.plan
    cleaning = None
    if view.cleaning is not None:
        cleaning = CleaningPlanOut(
            revision=view.cleaning.revision,
            policy=view.cleaning.policy,
            status=view.cleaning.status,
            decided_by_you=view.cleaning.decided_by_you,
            decided_at=view.cleaning.decided_at,
            operations=[
                CleaningOperationOut.model_validate(
                    {**op, "label": CATALOGUE[CleaningKind(op["kind"])].label}
                )
                for op in view.cleaning.operations
            ],
        )
    return PlanOut.model_validate(
        {
            "run": to_run_out(view.run),
            "revision": view.revision,
            "source": view.source,
            "provider": view.provider,
            "model": view.model,
            "prompt_version": view.prompt_version,
            "fallback_reason": view.fallback_reason,
            "dataset_summary": plan.get("dataset_summary", ""),
            "domain": plan.get("domain", ""),
            "grain": plan.get("grain", ""),
            "dictionary": plan.get("dictionary", []),
            "target": plan.get("target"),
            "features": plan.get("features", []),
            "analyses": plan.get("analyses", []),
            "charts": plan.get("charts", []),
            "relevance": plan.get("relevance", {}),
            "rejected": view.rejected,
            "model_input": view.model_input,
            "columns": [PlanColumnOut.model_validate(c) for c in view.columns],
            "cleaning": cleaning,
            "requests": plan.get("requests", []),
            "removed_analyses": plan.get("removed_analyses", []),
        }
    )


@router.get(
    "/{run_id}/plan",
    response_model=PlanOut,
    summary="The run's analysis plan, columns and cleaning plan",
    responses={409: problem_response()},
)
async def get_plan(
    run_id: UUID, principal: CurrentPrincipal, services: ServicesDep, response: Response
) -> PlanOut:
    """Available once the 'understand' step has finished (409 ``plan-not-ready`` before)."""
    view = await services.plans.get_plan(principal, run_id)
    response.headers["ETag"] = etag(view.run.run.row_version)
    return to_plan_out(view)


@router.post(
    "/{run_id}/cleaning-plan:decide",
    response_model=RunOut,
    summary="Accept or reject each cleaning change and approve the plan",
    responses={409: problem_response(), 412: problem_response()},
)
async def decide_cleaning_plan(  # noqa: PLR0913, PLR0917  (FastAPI dependencies)
    run_id: UUID,
    body: DecideCleaningRequest,
    principal: CurrentPrincipal,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> RunOut:
    """Every change waiting for a decision must be accepted or rejected. The decision is
    recorded as a new cleaning plan revision (who and when), the person's names, units,
    exclusions, target and features are saved for the dataset's next runs, and the run
    continues. ``412`` when the run or its cleaning plan changed since you loaded them."""
    detail = await services.plans.decide(
        principal,
        run_id,
        PlanDecision(
            cleaning_revision=body.cleaning_revision,
            decisions={d.operation_id: d.accept for d in body.decisions},
            columns=[
                ColumnEdit(
                    name=c.name, display_name=c.display_name, unit=c.unit, excluded=c.excluded
                )
                for c in body.columns
            ],
            target=body.target,
            no_target=body.no_target,
            disabled_features=body.disabled_features,
        ),
        if_match=parse_if_match(if_match),
    )
    response.headers["ETag"] = etag(detail.run.row_version)
    return to_run_out(detail)


@router.post(
    "/{run_id}/plan:revise",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Plan again with your changes; the run comes back for review",
    responses={409: problem_response(), 412: problem_response()},
)
async def revise_plan(  # noqa: PLR0913, PLR0917  (FastAPI dependencies)
    run_id: UUID,
    body: RevisePlanRequest,
    principal: CurrentPrincipal,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> RunOut:
    """Saves your cleaning decisions so far, column edits, removed questions and requests in
    your own words, then plans again: the engine first, then the planning model if one is
    configured. Finished steps whose inputs did not change are reused. The run returns to
    review with an outcome for every request. ``412`` when the run or its cleaning plan
    changed since you loaded them."""
    detail = await services.plans.revise(
        principal,
        run_id,
        PlanDecision(
            cleaning_revision=body.cleaning_revision,
            decisions={d.operation_id: d.accept for d in body.decisions},
            columns=[
                ColumnEdit(
                    name=c.name, display_name=c.display_name, unit=c.unit, excluded=c.excluded
                )
                for c in body.columns
            ],
            target=body.target,
            no_target=body.no_target,
            disabled_features=body.disabled_features,
            removed_analyses=body.removed_analyses,
            requests=body.requests,
        ),
        if_match=parse_if_match(if_match),
    )
    response.headers["ETag"] = etag(detail.run.row_version)
    return to_run_out(detail)


def to_report_out(view: ReportView) -> ReportOut:
    return ReportOut.model_validate(
        {
            **view.document,
            "run": to_run_out(view.run),
            "revision": view.revision,
            "created_at": view.created_at,
            "facts": view.facts,
        }
    )


@router.get(
    "/{run_id}/report",
    response_model=ReportOut,
    summary="The finished run's report: answers, charts and the facts they cite",
    responses={409: problem_response()},
)
async def get_report(run_id: UUID, principal: CurrentPrincipal, services: ServicesDep) -> ReportOut:
    """Available once the analysis has finished (409 ``report-not-ready`` before). Every
    sentence lists the ids of the facts it rests on; ``facts`` holds each fact's statement,
    columns, the rows it rests on and how it was computed."""
    return to_report_out(await services.reports.get_report(principal, run_id))


@router.get(
    "/{run_id}/recipe",
    response_class=PlainTextResponse,
    summary="Download the run's Python recipe (cleaning, features, charts)",
    responses={
        200: {"content": {"text/x-python": {}}, "description": "A runnable Python script."},
        409: problem_response(),
    },
)
async def get_recipe(
    run_id: UUID, principal: CurrentPrincipal, services: ServicesDep
) -> PlainTextResponse:
    """The approved plan as code: load the original file, clean it, add features, draw the
    charts. Generated from the stored plan; nothing in it was written by a model."""
    filename, source = await services.plans.recipe(principal, run_id)
    return PlainTextResponse(
        source,
        media_type="text/x-python; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# --------------------------------------------------------------------------- live progress


async def get_ticket(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_ticket_bearer)],
) -> EventTicket:
    if credentials is None:
        raise Unauthenticated(
            "Send the event ticket as 'Authorization: Bearer <ticket>'.",
            hint="Reload the page to get a new ticket.",
        )
    return await services.ticket_verifier.verify_ticket(credentials.credentials)


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


@events_router.get(
    "/{run_id}/events",
    summary="Live progress (Server-Sent Events) with replay from Last-Event-ID",
    response_class=EventStreamResponse,
    responses={
        200: {
            "model": RunEventOut,
            "description": "One `RunEventOut` per message: `id` is its sequence number, `event` "
            "its type and `data` its JSON. Comment lines keep the connection alive. The stream "
            "ends when the run finishes or the ticket expires; reconnect with `Last-Event-ID` "
            "to continue where it stopped.",
        }
    },
)
async def stream_events(
    run_id: UUID,
    services: ServicesDep,
    ticket: Annotated[EventTicket, Depends(get_ticket)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=20)] = None,
) -> StreamingResponse:
    if ticket.run_id != run_id:
        # The ticket is for another run: answer as if this run did not exist.
        raise RunNotFound("No run with this id exists in your organisation.")
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    # Checks access before the stream starts, so failures are normal problem responses.
    await services.runs.read_events(ticket.principal, run_id, after=after, limit=1)
    return EventStreamResponse(
        _event_stream(services, ticket, run_id, after),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    services: Services, ticket: EventTicket, run_id: UUID, after: int
) -> AsyncIterator[str]:
    keepalive = services.settings.events_keepalive_seconds
    yield f"retry: {RECONNECT_MS}\n\n"
    with services.broker.subscribe(run_id) as subscription:
        while True:
            try:
                batch = await services.runs.read_events(ticket.principal, run_id, after=after)
            except DomainError as exc:
                # Headers are sent: the only way to report is to end; the client reconnects.
                logger.warning("event stream ended early", extra={"code": exc.code})
                return
            for event in batch.events:
                yield format_event(event)
                after = event.seq
            if len(batch.events) == EVENT_BATCH:
                continue  # more are waiting
            if RunStatus(batch.status).terminal:
                return  # everything up to the final event has been sent
            remaining = (ticket.expires_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return  # the client fetches a new ticket and reconnects with Last-Event-ID
            if not await subscription.wait(min(keepalive, remaining)):
                yield ": keep-alive\n\n"


def format_event(event: RunEventInfo) -> str:
    payload: dict[str, Any] = {
        "seq": event.seq,
        "type": event.type,
        "stage": event.stage,
        "data": event.data,
        "at": event.created_at.isoformat(),
    }
    data = json.dumps(payload, separators=(",", ":"), default=str)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"
