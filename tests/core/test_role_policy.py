"""
Dedicated coverage for core/role_policy.py -- previously missing per the
2026-09-15 role-permission-attribute-architecture audit
(.collab/tasks/role-permission-attribute-architecture.md), flagged again
after the third round's finding: warehouse_scope_compliance_gap silently
returned "no gap" for a role not declared in WAREHOUSE_SCOPED_ROLE_NAMES
(e.g. 'accountant'), a false pass for exactly the "role about to become
warehouse-scoped" case the pre-deploy script exists to check.
"""
import pytest

from core import role_policy


# ── check_warehouse_scope ───────────────────────────────────────────────────

@pytest.mark.parametrize("role_name,allowed_codes,requested_code,expect_rejected", [
    ("warehouseman", None, None, True),           # scoped, no codes at all -> fail closed
    ("warehouseman", [], "JFK", True),             # scoped, empty codes -> fail closed
    ("warehouse_admin", None, "NJ", True),         # scoped, no codes -> fail closed
    ("warehouse_admin", ["JFK"], "NJ", True),      # scoped, out of assigned scope
    ("warehouse_admin", ["JFK", "NJ"], "NJ", False),  # scoped, in assigned scope
    ("warehouse_admin", ["JFK"], None, False),     # scoped, nothing specific requested
    ("admin", None, "DE", False),                  # unrestricted role, no codes needed
    ("customer", None, None, False),               # unrestricted role
    (None, ["JFK", "NJ"], "DE", True),              # no role key at all, but codes present and violated
])
def test_check_warehouse_scope_matrix(role_name, allowed_codes, requested_code, expect_rejected):
    result = role_policy.check_warehouse_scope(role_name, allowed_codes, requested_code)
    assert (result is not None) == expect_rejected


# ── normalize_assignment_fields ─────────────────────────────────────────────

def test_normalize_assignment_fields_clears_fields_for_unrelated_role():
    result = role_policy.normalize_assignment_fields(None, "admin", {
        "warehouse_codes": ["JFK"], "billing_customer_id": "F000001",
    })
    assert result == {"warehouse_codes": None, "billing_customer_id": None}


def test_normalize_assignment_fields_cleans_warehouse_codes_for_scoped_role():
    result = role_policy.normalize_assignment_fields(None, "warehouse_admin", {
        "warehouse_codes": ["NJ", " JFK "], "billing_customer_id": None,
    })
    assert result["warehouse_codes"] == ["JFK", "NJ"]


def test_normalize_assignment_fields_raises_with_field_attribution():
    with pytest.raises(role_policy.RoleAssignmentError) as exc_info:
        role_policy.normalize_assignment_fields(None, "warehouse_admin", {
            "warehouse_codes": None, "billing_customer_id": None,
        })
    assert exc_info.value.field == "warehouse_codes"
    assert exc_info.value.reason == "missing"


def test_field_descriptors_for_role_generic_per_role():
    assert [d["field_name"] for d in role_policy.field_descriptors_for_role("warehouse_admin")] == ["warehouse_codes"]
    assert [d["field_name"] for d in role_policy.field_descriptors_for_role("fedex_label_agent")] == ["billing_customer_id"]
    assert role_policy.field_descriptors_for_role("admin") == []


# ── warehouse_scope_compliance_gap / warehouse_grant_impact ────────────────

def test_compliance_gap_rejects_undeclared_role_without_touching_db():
    class _ExplodingDB:
        def query(self, *a, **kw):
            raise AssertionError("must not query the database for an undeclared role")

    with pytest.raises(ValueError):
        role_policy.warehouse_scope_compliance_gap(_ExplodingDB(), "accountant")


def test_grant_impact_returns_none_for_unscoped_role_without_raising():
    class _ExplodingDB:
        def query(self, *a, **kw):
            raise AssertionError("must not query the database when the role isn't warehouse-scoped")

    # 'accountant' is not in WAREHOUSE_SCOPED_ROLE_NAMES -- granting it an
    # unrelated (or even a warehouse-scoped) service must be a normal no-op,
    # never the ValueError that warehouse_scope_compliance_gap now raises
    # for an undeclared role.
    assert role_policy.warehouse_grant_impact(_ExplodingDB(), "accountant", "move_storage") is None


def test_grant_impact_returns_none_for_unrelated_service_without_raising():
    class _ExplodingDB:
        def query(self, *a, **kw):
            raise AssertionError("must not query the database for an unrelated service")

    assert role_policy.warehouse_grant_impact(_ExplodingDB(), "warehouse_admin", "fedex_label") is None


class _FakeRole:
    def __init__(self, role_id):
        self.role_id = role_id


class _FakeCountQuery:
    def __init__(self, count_value):
        self.count_value = count_value

    def filter(self, *a, **kw):
        return self

    def count(self):
        return self.count_value

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._first_value


class _FakeRoleLookupQuery(_FakeCountQuery):
    def __init__(self, role):
        self._first_value = role


class _FakeComplianceDB:
    """
    Minimal fake supporting exactly the three query shapes
    warehouse_scope_compliance_gap issues: Role lookup (.filter_by().first()),
    and two count queries (GroupMember/KefuStaff, .filter().filter().count()).
    """

    def __init__(self, role, member_gap_count, staff_gap_count):
        self.role = role
        self.member_gap_count = member_gap_count
        self.staff_gap_count = staff_gap_count

    def query(self, model):
        from models.role import Role
        from models.group import GroupMember
        from models.kefu import KefuStaff
        if model is Role:
            return _FakeRoleLookupQuery(self.role)
        if model is GroupMember:
            return _FakeCountQuery(self.member_gap_count)
        if model is KefuStaff:
            return _FakeCountQuery(self.staff_gap_count)
        raise AssertionError(f"unexpected query for {model}")


def test_compliance_gap_reports_counts_for_declared_role_with_gaps():
    db = _FakeComplianceDB(role=_FakeRole("role-1"), member_gap_count=2, staff_gap_count=1)
    gap = role_policy.warehouse_scope_compliance_gap(db, "warehouse_admin")
    assert gap == {"member_count": 2, "staff_count": 1}


def test_compliance_gap_none_for_declared_role_with_no_gaps():
    db = _FakeComplianceDB(role=_FakeRole("role-1"), member_gap_count=0, staff_gap_count=0)
    assert role_policy.warehouse_scope_compliance_gap(db, "warehouse_admin") is None


def test_grant_impact_delegates_to_compliance_gap_for_scoped_role_and_service():
    db = _FakeComplianceDB(role=_FakeRole("role-1"), member_gap_count=3, staff_gap_count=0)
    impact = role_policy.warehouse_grant_impact(db, "warehouse_admin", "move_storage")
    assert impact == {"member_count": 3, "staff_count": 0}
