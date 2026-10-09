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
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from insightfulcell.api.container import Services
from insightfulcell.api.deps import CurrentPrincipal, IdempotencyKey, ServicesDep
from insightfulcell.api.problems import (
    PROBLEM_ONLY_RESPONSES,
    PROBLEM_RESPONSES,
    problem_response,
)
from insightfulcell.api.schemas import (
    CreateRunRequest,
    RunEventOut,
    RunOut,
    RunPageOut,
    RunSummaryOut,
    StageOut,
)
from insightfulcell.auth.tokens import EventTicket
from insightfulcell.domain.errors import DomainError, RunChanged, RunNotFound, Unauthenticated
from insightfulcell.domain.runs import RunStatus
from insightfulcell.services.datasets import MAX_PAGE_SIZE
from insightfulcell.services.dto import RunDetail, RunEventInfo
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
