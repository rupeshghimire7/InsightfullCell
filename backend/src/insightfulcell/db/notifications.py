"""One ``LISTEN`` connection per API instance, fanned out to the event streams it serves (IC-008).

The worker appends a ``run_events`` row; an insert trigger sends ``NOTIFY ic_run_events`` with
``{"run_id", "seq"}``. This broker wakes every stream subscribed to that run, and the stream then
reads the new rows itself, inside its own tenant-scoped transaction. Notifications carry no
tenant data and are only a wake-up call: streams also re-check on every keep-alive, so a missed
notification (the connection dropped and is being re-established) delays an event, never loses it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator
from typing import Final
from uuid import UUID

import psycopg

from insightfulcell.logging_config import exception_summary

logger = logging.getLogger(__name__)

CHANNEL: Final = "ic_run_events"
_MAX_BACKOFF_SECONDS: Final = 30.0


class Subscription:
    """Wakes when the run it watches has new events."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        self._event.set()

    async def wait(self, seconds: float) -> bool:
        """``True`` when woken by a notification, ``False`` after ``seconds`` without one."""
        try:
            await asyncio.wait_for(self._event.wait(), seconds)
        except TimeoutError:
            return False
        self._event.clear()
        return True


class EventBroker:
    def __init__(self, conninfo: str | None, *, connect_timeout: int = 5) -> None:
        """``conninfo=None`` disables listening: streams then rely on their keep-alive checks."""
        self._conninfo = conninfo
        self._connect_timeout = connect_timeout
        self._subscribers: dict[UUID, set[Subscription]] = {}
        self._task: asyncio.Task[None] | None = None
        self.connected = asyncio.Event()

    async def start(self) -> None:
        if self._conninfo is not None and self._task is None:
            self._task = asyncio.create_task(self._listen(), name="run-event-listener")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.connected.clear()

    @contextlib.contextmanager
    def subscribe(self, run_id: UUID) -> Iterator[Subscription]:
        subscription = Subscription()
        self._subscribers.setdefault(run_id, set()).add(subscription)
        try:
            yield subscription
        finally:
            watchers = self._subscribers.get(run_id)
            if watchers is not None:
                watchers.discard(subscription)
                if not watchers:
                    del self._subscribers[run_id]

    def dispatch(self, payload: str) -> None:
        try:
            run_id = UUID(str(json.loads(payload)["run_id"]))
        except (ValueError, KeyError, TypeError):
            logger.warning("ignored a malformed run-event notification")
            return
        for subscription in self._subscribers.get(run_id, ()):
            subscription.notify()

    def _wake_everyone(self) -> None:
        for watchers in self._subscribers.values():
            for subscription in watchers:
                subscription.notify()

    async def _listen(self) -> None:
        assert self._conninfo is not None  # noqa: S101  (start() checks it)
        backoff = 1.0
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True, connect_timeout=self._connect_timeout
                ) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    self.connected.set()
                    backoff = 1.0
                    # Anything committed while we were disconnected is read now.
                    self._wake_everyone()
                    async for notification in connection.notifies():
                        self.dispatch(notification.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "run-event listener disconnected; reconnecting",
                    extra={"retry_in_seconds": backoff, **exception_summary(exc)},
                )
            self.connected.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
