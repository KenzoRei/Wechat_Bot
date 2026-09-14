"""
role_change's three hardened boundaries
(before-persistence, pre-confirm, execution) plus last-admin-protection
must dispatch on the tagged target identity ({kind, key}), never by
probing which table contains a matching raw string. Mock DB only, matching
this project's
offline-test pattern.
"""
from types import SimpleNamespace

import pytest

from core import pre_confirm_validators, workflow_engine
from core.role_identity import tag_kefu_identity
from handlers.uchoice.role_change import RoleChangeHandler
from models.customer import Customer
from models.group import GroupMember
from models.kefu import KefuStaff
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
            if openid in self.db.smart_robot_members:
                role_name, is_active = self.db.smart_robot_members[openid]
                return SimpleNamespace(role_id=f"role-{role_name}", is_active=is_active)
            return None
        if self.model is KefuStaff:
            staff_id = self.filters.get("staff_id")
            if staff_id in self.db.kefu_members:
                role_name, is_active = self.db.kefu_members[staff_id]
                return SimpleNamespace(
                    staff_id=staff_id,
                    role_id=f"role-{role_name}",
                    warehouse_codes=None,
                    billing_customer_id=self.db.kefu_billing.get(staff_id),
                    is_active=is_active,
                )
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
        role_id = self.filters.get("role_id")
        wanted_role = role_id.replace("role-", "") if role_id else None
        wanted_active = self.filters.get("is_active")
        if self.model is GroupMember:
            return sum(
                1 for role_name, is_active in self.db.smart_robot_members.values()
                if role_name == wanted_role and (wanted_active is None or is_active == wanted_active)
            )
        if self.model is KefuStaff:
            return sum(
                1 for role_name, is_active in self.db.kefu_members.values()
                if role_name == wanted_role and (wanted_active is None or is_active == wanted_active)
            )
        return 0


class _MockDB:
    def __init__(self, smart_robot_members=None, kefu_members=None, kefu_billing=None, customers=None):
        # each dict: {key: role_name} or {key: (role_name, is_active)}
        self.smart_robot_members = {
            k: (v if isinstance(v, tuple) else (v, True))
            for k, v in (smart_robot_members or {}).items()
        }
        self.kefu_members = {
            k: (v if isinstance(v, tuple) else (v, True))
            for k, v in (kefu_members or {}).items()
        }
        self.kefu_billing = dict(kefu_billing or {})
        # {customer_id: status}, defaults to "active" -- matches core.
        # customer_directory.get_customer's db.get(Customer, id) call.
        self.customers = dict(customers or {})
        self.all_roles = {"admin", "customer", "warehouseman", "accountant", "pending", "fedex_label_agent"}
        self.committed = False

    def query(self, model):
        return _Query(self, model)

    def get(self, model, pk):
        if model is Customer and pk in self.customers:
            status = self.customers[pk]
            return SimpleNamespace(
                customer_id=pk, display_name="Test Co", display_name_cn=None, contact_name=None,
                email=None, phone=None, addr_line1=None, city=None, state=None, zip=None, country=None,
                bank_account=None, status=status, rate_multiplier={}, ydd_channel_id={}, oms_wh_code=None,
                toggles={}, notes=None,
            )
        return None

    def commit(self):
        self.committed = True

    def execute(self, *args, **kwargs):
        """No-op: lock_group_admin_invariant's advisory lock has no meaning against this in-memory mock."""
        return None

    def refresh(self, obj):
        """No-op: this mock's SimpleNamespace already reflects the current in-memory state."""
        return None


KEFU_ID = "11111111-1111-1111-1111-111111111111"


# ── before-persistence dispatch ─────────────────────────────────────────────

def test_before_persistence_accepts_real_kefu_target():
    db = _MockDB(kefu_members={KEFU_ID: "customer"})
    result = workflow_engine._sanitize_role_change_fields_before_persistence(
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "warehouseman"}, db, group_id="g1",
    )
    assert result.get("target_openid") == tag_kefu_identity(KEFU_ID)


def test_before_persistence_drops_fabricated_kefu_target():
    db = _MockDB(kefu_members={KEFU_ID: "customer"})
    result = workflow_engine._sanitize_role_change_fields_before_persistence(
        {"target_openid": tag_kefu_identity("99999999-0000-0000-0000-000000000000")}, db, group_id="g1",
    )
    assert "target_openid" not in result


def test_before_persistence_does_not_confuse_kefu_and_smart_robot_ids():
    """A kefu staff_id and a smart-robot openid could coincidentally be the
    same raw string -- the tag must be what decides, not table-probing."""
    db = _MockDB(smart_robot_members={KEFU_ID: "customer"}, kefu_members={})
    # KEFU_ID exists as a Smart Robot member's raw openid, but NOT as a
    # kefu_staff.staff_id -- a tagged "kefu:" reference to it must still
    # fail, not silently match the Smart Robot row.
    result = workflow_engine._sanitize_role_change_fields_before_persistence(
        {"target_openid": tag_kefu_identity(KEFU_ID)}, db, group_id="g1",
    )
    assert "target_openid" not in result


# ── pre-confirm dispatch ─────────────────────────────────────────────────────

def test_pre_confirm_accepts_valid_kefu_warehouseman_promotion():
    db = _MockDB(kefu_members={KEFU_ID: "customer"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "warehouseman", "warehouse_codes": ["JFK"]}, db,
    )
    assert error is None


def test_pre_confirm_accepts_kefu_target_for_customer_role_with_valid_billing_id():
    """V31: KefuStaff gained billing_customer_id, closing the gap the old
    version of this test asserted (Kefu could never be assigned a
    customer-identity role at all). A real, active customer binding is
    now sufficient -- no channel-based rejection."""
    db = _MockDB(kefu_members={KEFU_ID: "warehouseman"}, customers={"F000001": "active"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "customer", "billing_customer_id": "F000001"}, db,
    )
    assert error is None


def test_pre_confirm_accepts_kefu_target_for_fedex_label_agent_with_valid_billing_id():
    """fedex_label_agent is also a customer-identity role (a narrower one,
    FedEx only) -- must be accepted the same way as plain 'customer',
    proving this isn't a one-off special case for that single role name."""
    db = _MockDB(kefu_members={KEFU_ID: "warehouseman"}, customers={"F000002": "active"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "fedex_label_agent", "billing_customer_id": "F000002"}, db,
    )
    assert error is None


def test_pre_confirm_rejects_kefu_target_for_customer_role_without_billing_id():
    """Missing binding is still rejected -- just for the right reason now
    (no billing_customer_id provided), not a blanket channel-based ban."""
    db = _MockDB(kefu_members={KEFU_ID: "admin"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "customer"}, db,
    )
    assert error is not None
    assert "客户编号" in error


def test_pre_confirm_rejects_kefu_target_for_customer_role_with_inactive_customer():
    db = _MockDB(kefu_members={KEFU_ID: "admin"}, customers={"F000003": "inactive"})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "customer", "billing_customer_id": "F000003"}, db,
    )
    assert error is not None


def test_pre_confirm_rejects_unknown_kefu_target():
    db = _MockDB(kefu_members={})
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "admin"}, db,
    )
    assert error is not None


def test_last_admin_protection_counts_across_both_channels():
    """The group's only admin is a kefu_staff row -- demoting the sole
    Smart Robot admin must still be blocked by counting the Kefu admin
    too, and vice versa."""
    db = _MockDB(
        smart_robot_members={"m1": "customer"},
        kefu_members={KEFU_ID: "admin"},
    )
    # Demoting the sole (Kefu) admin must be blocked.
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "accountant"}, db,
    )
    assert error is not None


def test_last_admin_protection_allows_demotion_when_another_admin_exists_in_other_channel():
    db = _MockDB(
        smart_robot_members={"m1": "admin"},
        kefu_members={KEFU_ID: "admin"},
    )
    # Two admins total (one per channel) -- demoting one is fine.
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "accountant"}, db,
    )
    assert error is None


def test_last_admin_protection_allows_reassigning_an_already_inactive_kefu_admin():
    """
    Mirrors the Smart Robot case in test_role_change_hardening.py: an inactive
    Kefu admin is not
    contributing to the active-admin count in the first place, so
    reassigning them removes nothing.
    """
    db = _MockDB(
        smart_robot_members={"active-admin": "admin"},
        kefu_members={KEFU_ID: ("admin", False)},
    )
    error = pre_confirm_validators.run(
        "role_change", {"group_id": "g1"},
        {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "accountant"}, db,
    )
    assert error is None


# ── execution backstop dispatch ─────────────────────────────────────────────

def test_execution_backstop_accepts_valid_kefu_promotion():
    db = _MockDB(kefu_members={KEFU_ID: "customer"})
    context = {
        "collected_fields": {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "warehouseman", "warehouse_codes": ["JFK"]},
        "group_id": "g1",
    }
    result = RoleChangeHandler().handle(context, {}, db)
    assert result == {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "warehouseman"}
    assert db.committed is True


def test_execution_backstop_rejects_kefu_target_not_a_member():
    db = _MockDB(kefu_members={})
    context = {
        "collected_fields": {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "admin"},
        "group_id": "g1",
    }
    with pytest.raises(RuntimeError):
        RoleChangeHandler().handle(context, {}, db)


def test_execution_backstop_persists_billing_customer_id_on_kefu_target():
    """The actual mutation: RoleChangeHandler must write billing_customer_id
    onto the KefuStaff row itself, not just validate it upstream."""
    db = _MockDB(kefu_members={KEFU_ID: "warehouseman"}, customers={"F000004": "active"})
    context = {
        "collected_fields": {
            "target_openid": tag_kefu_identity(KEFU_ID), "new_role": "fedex_label_agent",
            "billing_customer_id": "F000004",
        },
        "group_id": "g1",
    }
    result = RoleChangeHandler().handle(context, {}, db)
    assert result == {"target_openid": tag_kefu_identity(KEFU_ID), "new_role": "fedex_label_agent"}
    assert db.committed is True


def test_execution_backstop_still_works_for_smart_robot_targets():
    """Regression: the tagged dispatch must not break the existing,
    already-shipped Smart Robot path."""
    db = _MockDB(smart_robot_members={"m1": "customer"})
    context = {
        "collected_fields": {"target_openid": "m1", "new_role": "admin"},
        "group_id": "g1",
    }
    result = RoleChangeHandler().handle(context, {}, db)
    assert result == {"target_openid": "m1", "new_role": "admin"}
