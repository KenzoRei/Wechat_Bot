"""
phase4-self-registration.md Sec 4/5/8 -- role_change's three-boundary
hardening (before-persistence, pre-confirm, execution) and the pending-
exclusion invariant. Mock DB only, per current DB-test-policy restriction.
"""
from types import SimpleNamespace

import pytest

from core import pre_confirm_validators, workflow_engine
from handlers.uchoice.role_change import RoleChangeHandler
from models.group import GroupMember
from models.role import Role


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def first(self):
        if self.model is GroupMember:
            openid = self.filters.get("wechat_openid")
            if openid in self.db.members:
                role_name, is_active = self.db.members[openid]
                return SimpleNamespace(role_id=f"role-{role_name}", is_active=is_active)
            return None
        if self.model is Role:
            role_id = self.filters.get("role_id")
            name = self.filters.get("name")
            if role_id is not None:
                role_name = role_id.replace("role-", "")
                return SimpleNamespace(role_id=role_id, name=role_name) if role_name in self.db.all_roles else None
            if name is not None:
                return SimpleNamespace(role_id=f"role-{name}", name=name) if name in self.db.all_roles else None
        return None

    def count(self):
        if self.model is GroupMember:
            role_id = self.filters.get("role_id")
            wanted_role = role_id.replace("role-", "") if role_id else None
            wanted_active = self.filters.get("is_active")
            return sum(
                1 for role_name, is_active in self.db.members.values()
                if role_name == wanted_role and (wanted_active is None or is_active == wanted_active)
            )
        return 0


class _MockDB:
    def __init__(self, members=None):
        # members: {wechat_openid: role_name} or {wechat_openid: (role_name, is_active)}
        raw = members or {"m1": "customer"}
        self.members = {
            openid: (v if isinstance(v, tuple) else (v, True))
            for openid, v in raw.items()
        }
        self.all_roles = {"admin", "customer", "warehouseman", "accountant", "pending"}
        self.committed = False

    def query(self, model):
        return _Query(self, model)

    def commit(self):
        self.committed = True

    def execute(self, *args, **kwargs):
        """No-op: lock_group_admin_invariant's advisory lock has no meaning against this in-memory mock."""
        return None

    def refresh(self, obj):
        """No-op: this mock's SimpleNamespace already reflects self.members' current state."""
        return None


# ── before-persistence (core/workflow_engine.py) ────────────────────────────

def test_before_persistence_drops_fabricated_target_openid():
    db = _MockDB(members={"m1": "customer"})
    result = workflow_engine._sanitize_extracted_fields_before_persistence(
        "role_change", {"target_openid": "ghost", "new_role": "admin"}, db, group_id="g1",
    )
    assert "target_openid" not in result
    assert result.get("new_role") == "admin"  # unrelated valid field preserved


def test_before_persistence_keeps_real_target_openid():
    db = _MockDB(members={"m1": "customer"})
    result = workflow_engine._sanitize_extracted_fields_before_persistence(
        "role_change", {"target_openid": "m1", "new_role": "admin"}, db, group_id="g1",
    )
    assert result.get("target_openid") == "m1"


def test_before_persistence_drops_non_assignable_new_role():
    db = _MockDB(members={"m1": "customer"})
    result = workflow_engine._sanitize_extracted_fields_before_persistence(
        "role_change", {"target_openid": "m1", "new_role": "pending"}, db, group_id="g1",
    )
    assert "new_role" not in result
    assert result.get("target_openid") == "m1"  # unrelated valid field preserved


def test_before_persistence_drops_fabricated_role_name():
    db = _MockDB(members={"m1": "customer"})
    result = workflow_engine._sanitize_extracted_fields_before_persistence(
        "role_change", {"new_role": "superadmin"}, db, group_id="g1",
    )
    assert "new_role" not in result


# ── pre-confirm (core/pre_confirm_validators.py) ────────────────────────────

def test_pre_confirm_rejects_fabricated_target():
    db = _MockDB(members={"m1": "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"}, {"target_openid": "ghost", "new_role": "admin"}, db,
    )
    assert error is not None


def test_pre_confirm_rejects_pending_as_new_role():
    db = _MockDB(members={"m1": "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"}, {"target_openid": "m1", "new_role": "pending"}, db,
    )
    assert error is not None


def test_pre_confirm_rejects_warehouseman_without_warehouse_code():
    db = _MockDB(members={"m1": "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "m1", "new_role": "warehouseman"}, db,
    )
    assert error is not None


def test_pre_confirm_rejects_warehouseman_with_invalid_warehouse_code():
    db = _MockDB(members={"m1": "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "m1", "new_role": "warehouseman", "warehouse_code": "ATL"}, db,
    )
    assert error is not None


def test_pre_confirm_accepts_valid_warehouseman_promotion():
    db = _MockDB(members={"m1": "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "m1", "new_role": "warehouseman", "warehouse_code": "JFK"}, db,
    )
    assert error is None


def test_last_admin_protection_still_holds():
    db = _MockDB(members={"only-admin": "admin"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "only-admin", "new_role": "customer"}, db,
    )
    assert error is not None


def test_last_admin_protection_allows_reassigning_an_already_inactive_admin():
    """
    Codex round-4 review: an admin who is currently is_active=False must
    not be treated as "the last active admin" -- they aren't active at
    all, so reassigning their role removes nothing from the active count.
    """
    db = _MockDB(members={
        "active-admin": "admin",
        "inactive-admin": ("admin", False),
    })
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "inactive-admin", "new_role": "customer"}, db,
    )
    assert error is None


def test_last_admin_protection_inactive_admin_does_not_count_toward_the_total():
    """The one real ACTIVE admin is still protected even though a second, inactive admin row also exists."""
    db = _MockDB(members={
        "only-active-admin": "admin",
        "inactive-admin": ("admin", False),
    })
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": "only-active-admin", "new_role": "customer"}, db,
    )
    assert error is not None


# ── execution backstop (handlers/uchoice/role_change.py) ───────────────────

def test_execution_backstop_rejects_pending_even_if_upstream_bypassed():
    db = _MockDB(members={"m1": "customer"})
    context = {"collected_fields": {"target_openid": "m1", "new_role": "pending"}, "group_id": "g1"}
    with pytest.raises(RuntimeError):
        RoleChangeHandler().handle(context, {}, db)


def test_execution_backstop_rejects_warehouseman_without_warehouse_code():
    db = _MockDB(members={"m1": "customer"})
    context = {"collected_fields": {"target_openid": "m1", "new_role": "warehouseman"}, "group_id": "g1"}
    with pytest.raises(RuntimeError):
        RoleChangeHandler().handle(context, {}, db)


def test_execution_backstop_rejects_warehouseman_with_invalid_warehouse_code():
    db = _MockDB(members={"m1": "customer"})
    context = {
        "collected_fields": {"target_openid": "m1", "new_role": "warehouseman", "warehouse_code": "ATL"},
        "group_id": "g1",
    }
    with pytest.raises(RuntimeError):
        RoleChangeHandler().handle(context, {}, db)


def test_execution_backstop_accepts_valid_warehouseman_promotion():
    db = _MockDB(members={"m1": "customer"})
    context = {
        "collected_fields": {"target_openid": "m1", "new_role": "warehouseman", "warehouse_code": "JFK"},
        "group_id": "g1",
    }
    result = RoleChangeHandler().handle(context, {}, db)
    assert result == {"target_openid": "m1", "new_role": "warehouseman"}
    assert db.committed is True


def test_execution_backstop_rejects_target_not_a_member():
    db = _MockDB(members={})
    context = {"collected_fields": {"target_openid": "ghost", "new_role": "admin"}, "group_id": "g1"}
    with pytest.raises(RuntimeError):
        RoleChangeHandler().handle(context, {}, db)
