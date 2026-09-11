"""
core/role_registry.py -- the relocated, richer replacement for
core.uchoice_constants.ASSIGNABLE_ROLE_NAMES (a bare frozenset). Pure,
offline, no DB access.
"""
from core.role_registry import ASSIGNABLE_ROLES, ASSIGNABLE_ROLE_NAMES


def test_assignable_role_names_matches_assignable_roles():
    assert ASSIGNABLE_ROLE_NAMES == {r.name for r in ASSIGNABLE_ROLES}


def test_pending_is_not_assignable():
    assert "pending" not in ASSIGNABLE_ROLE_NAMES


def test_label_agent_is_assignable():
    assert "label_agent" in ASSIGNABLE_ROLE_NAMES


def test_every_assignable_role_has_a_description():
    for role in ASSIGNABLE_ROLES:
        assert role.description
