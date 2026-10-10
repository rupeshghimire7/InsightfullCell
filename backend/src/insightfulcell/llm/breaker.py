"""A circuit breaker per provider: when failures spike, stop calling for a while.

Closed: calls go through. After ``threshold`` failures within ``window`` seconds it opens: calls
fail fast (the caller falls back to its deterministic path) for ``open_seconds``. Then one call
is let through (half-open); success closes the breaker, failure opens it again.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int,
        open_seconds: float,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._open_seconds = open_seconds
        self._window = window
        self._clock = clock
        self._failures: deque[float] = deque()
        self._open_until: float | None = None
        self._trial = False

    @property
    def state(self) -> str:
        if self._open_until is None:
            return "closed"
        return "open" if self._clock() < self._open_until else "half_open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial:
            self._trial = True  # exactly one trial call
            return True
        return False

    def success(self) -> None:
        self._failures.clear()
        self._open_until = None
        self._trial = False

    def failure(self) -> None:
        now = self._clock()
        if self.state == "half_open":
            self._open(now)
            return
        self._failures.append(now)
        while self._failures and self._failures[0] < now - self._window:
            self._failures.popleft()
        if len(self._failures) >= self._threshold:
            self._open(now)

    def _open(self, now: float) -> None:
        self._open_until = now + self._open_seconds
        self._failures.clear()
        self._trial = False
