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
    RUN_CREATE = "run:create"
    RUN_READ = "run:read"
    RUN_CANCEL = "run:cancel"
    CLEANING_DECIDE = "cleaning:decide"
    DRILLDOWN_CREATE = "drilldown:create"


_ALL: Final = frozenset(Role)
_WRITERS: Final = frozenset({Role.OWNER, Role.ADMIN, Role.ANALYST})

POLICY: Final[dict[Action, frozenset[Role]]] = {
    Action.UPLOAD_CREATE: _WRITERS,
    Action.UPLOAD_READ: _WRITERS,
    Action.DATASET_READ: _ALL,
    # Starting, retrying and cancelling analyses is analyst work; viewers read results only.
    Action.RUN_CREATE: _WRITERS,
    Action.RUN_READ: _ALL,
    Action.RUN_CANCEL: _WRITERS,
    # Approving cleaning changes is analyst work; viewers read plans but never decide them.
    Action.CLEANING_DECIDE: _WRITERS,
    # Exploring a subset starts a new (exploratory) run: analyst work.
    Action.DRILLDOWN_CREATE: _WRITERS,
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
            hint="Ask an organisation owner or admin to change your role.",
        )
