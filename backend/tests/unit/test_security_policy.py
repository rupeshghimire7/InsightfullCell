from __future__ import annotations

from uuid import uuid4

import pytest

from insightfulcell.domain.errors import Forbidden
from insightfulcell.domain.security import POLICY, Action, Principal, Role, require

# The endpoint x role matrix, spelled out so a policy change shows up as a test diff.
EXPECTED: dict[Action, set[Role]] = {
    Action.UPLOAD_CREATE: {Role.OWNER, Role.ADMIN, Role.ANALYST},
    Action.UPLOAD_READ: {Role.OWNER, Role.ADMIN, Role.ANALYST},
    Action.DATASET_READ: {Role.OWNER, Role.ADMIN, Role.ANALYST, Role.VIEWER},
    Action.RUN_CREATE: {Role.OWNER, Role.ADMIN, Role.ANALYST},
    Action.RUN_READ: {Role.OWNER, Role.ADMIN, Role.ANALYST, Role.VIEWER},
    Action.RUN_CANCEL: {Role.OWNER, Role.ADMIN, Role.ANALYST},
    Action.CLEANING_DECIDE: {Role.OWNER, Role.ADMIN, Role.ANALYST},
    Action.DRILLDOWN_CREATE: {Role.OWNER, Role.ADMIN, Role.ANALYST},
}


def test_every_action_has_a_policy_entry() -> None:
    assert set(POLICY) == set(Action)


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("role", list(Role))
def test_role_matrix(action: Action, role: Role) -> None:
    principal = Principal(user_id=uuid4(), org_id=uuid4(), role=role)
    if role in EXPECTED[action]:
        require(principal, action)
    else:
        with pytest.raises(Forbidden) as info:
            require(principal, action)
        assert role.value in info.value.detail
        assert info.value.hint
