"""Roles, actions and the central authorization policy (FR-AUTH-03, NFR-SEC-01).

All role checks live in this one table, not in scattered ``if`` statements, so the
endpoint x role matrix can be reviewed and tested in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from insightfulcell.domain.errors import Forbidden


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Action(StrEnum):
    UPLOAD_CREATE = "upload:create"
    UPLOAD_READ = "upload:read"
    DATASET_READ = "dataset:read"
    DATASET_DELETE = "dataset:delete"
    RUN_CREATE = "run:create"
    RUN_READ = "run:read"
    RUN_CANCEL = "run:cancel"
    RUN_DELETE = "run:delete"
    CLEANING_DECIDE = "cleaning:decide"
    DRILLDOWN_CREATE = "drilldown:create"
    DATA_READ = "data:read"
    WORKSPACE_DELETE = "workspace:delete"


_ALL: Final = frozenset(Role)
_WRITERS: Final = frozenset({Role.OWNER, Role.ADMIN, Role.ANALYST})

POLICY: Final[dict[Action, frozenset[Role]]] = {
    Action.UPLOAD_CREATE: _WRITERS,
    Action.UPLOAD_READ: _WRITERS,
    Action.DATASET_READ: _ALL,
    # Deleting removes the work of everyone in the organisation: the people who may add it.
    Action.DATASET_DELETE: _WRITERS,
    # Starting, retrying and cancelling analyses is analyst work; viewers read results only.
    Action.RUN_CREATE: _WRITERS,
    Action.RUN_READ: _ALL,
    Action.RUN_CANCEL: _WRITERS,
    Action.RUN_DELETE: _WRITERS,
    # Approving cleaning changes is analyst work; viewers read plans but never decide them.
    Action.CLEANING_DECIDE: _WRITERS,
    # Exploring a subset starts a new (exploratory) run: analyst work.
    Action.DRILLDOWN_CREATE: _WRITERS,
    # The analysed table's rows (personal-data columns stay hidden): everyone who reads reports.
    Action.DATA_READ: _ALL,
    # "Delete my account" erases the whole workspace: only its owner (ADR-043).
    Action.WORKSPACE_DELETE: frozenset({Role.OWNER}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """The verified caller. Built only from a verified token, never from a request body."""

    user_id: UUID
    org_id: UUID
    role: Role
    org_name: str | None = None


def require(principal: Principal, action: Action) -> None:
    """Raise ``Forbidden`` unless the principal's role may perform ``action``."""
    if principal.role not in POLICY[action]:
        raise Forbidden(
            f"Your role ({principal.role.value}) cannot perform {action.value}.",
            hint="Ask a workspace owner or admin to change your role.",
        )
