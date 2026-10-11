from __future__ import annotations

import pytest

from insightfulcell.domain.errors import RunNotInState
from insightfulcell.domain.runs import (
    ACTIVE_STATUSES,
    CANCELLABLE,
    TERMINAL_STATUSES,
    TRANSITIONS,
    RunStatus,
    can_transition,
    require_status,
    require_transition,
    sources_for,
)

S = RunStatus

# The diagram in the system design, edge by edge. A change to the rules shows up here.
ALLOWED = {
    (S.QUEUED, S.RUNNING),
    (S.QUEUED, S.CANCELLED),
    (S.RUNNING, S.AWAITING_REVIEW),
    (S.RUNNING, S.SUCCEEDED),
    (S.RUNNING, S.FAILED),
    (S.RUNNING, S.CANCELLED),
    (S.AWAITING_REVIEW, S.QUEUED),
    (S.AWAITING_REVIEW, S.CANCELLED),
    (S.AWAITING_REVIEW, S.EXPIRED),
    (S.FAILED, S.QUEUED),
    (S.SUCCEEDED, S.QUEUED),  # report:renarrate, after a model outage only
}


@pytest.mark.parametrize("source", list(S))
@pytest.mark.parametrize("target", list(S))
def test_transition_table(source: RunStatus, target: RunStatus) -> None:
    assert can_transition(source, target) is ((source, target) in ALLOWED)


def test_every_status_has_an_entry() -> None:
    assert set(TRANSITIONS) == set(S)


def test_terminal_statuses_go_nowhere() -> None:
    assert {S.SUCCEEDED, S.FAILED, S.CANCELLED, S.EXPIRED} == TERMINAL_STATUSES
    for status in TERMINAL_STATUSES - {S.FAILED, S.SUCCEEDED}:
        assert TRANSITIONS[status] == frozenset()
    assert TRANSITIONS[S.SUCCEEDED] == {S.QUEUED}  # only to narrate the report again
    assert all(status.terminal for status in TERMINAL_STATUSES)
    assert not S.RUNNING.terminal


def test_active_and_cancellable() -> None:
    assert {S.QUEUED, S.RUNNING} == ACTIVE_STATUSES
    assert S.QUEUED.active
    assert not S.AWAITING_REVIEW.active
    assert {S.QUEUED, S.RUNNING, S.AWAITING_REVIEW} == CANCELLABLE
    assert sources_for(S.CANCELLED) == CANCELLABLE
    assert sources_for(S.QUEUED) == {S.AWAITING_REVIEW, S.FAILED, S.SUCCEEDED}


def test_refusals_explain_in_words() -> None:
    with pytest.raises(RunNotInState, match="A succeeded run cannot become running"):
        require_transition(S.SUCCEEDED, S.RUNNING)
    with pytest.raises(RunNotInState, match="This run is awaiting review, so it cannot be retried"):
        require_status(S.AWAITING_REVIEW, {S.FAILED}, "retried")
    require_transition(S.QUEUED, S.RUNNING)
    require_status(S.FAILED, {S.FAILED}, "retried")
