"""Mock DB for core.self_registration tests -- mirrors the style already
used in tests/uchoice_lifecycle/test_sku_validation_contracts.py and
tests/uchoice_storage_atomicity/test_typed_validators.py (no real DB
connection, per the current DB-test-policy restriction)."""

from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from models.group import GroupConfig, GroupMember
from models.role import Role


class _FakeDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeOrig:
    def __init__(self, constraint_name):
        self.diag = _FakeDiag(constraint_name)


def make_integrity_error(constraint_name):
    return IntegrityError("INSERT INTO group_member ...", {}, _FakeOrig(constraint_name))


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def first(self):
        if self.model is GroupConfig:
            if self.db.group_active and self.filters.get("wechat_group_id") == self.db.wechat_group_id:
                return SimpleNamespace(group_id=self.db.group_id)
            return None
        if self.model is GroupMember:
            if self.db.existing_member_role is not None:
                return SimpleNamespace(role_id=f"role-{self.db.existing_member_role}")
            return None
        if self.model is Role:
            role_id = self.filters.get("role_id")
            name = self.filters.get("name")
            if role_id is not None:
                return SimpleNamespace(name=role_id.replace("role-", "")) if role_id.startswith("role-") else None
            if name == "pending":
                return SimpleNamespace(role_id="role-pending") if self.db.pending_role_exists else None
            return None
        return None


class MockDB:
    """
    group_active: whether the GroupConfig lookup succeeds.
    existing_member_role: None (not a member yet) or a role name string
      (e.g. "pending", "admin") for an already-registered sender.
    pending_role_exists: whether the 'pending' role row exists.
    integrity_error_constraint: if set, the first commit() call raises an
      IntegrityError with this constraint name; subsequent commits succeed.
    """

    def __init__(self, group_active=True, existing_member_role=None,
                 pending_role_exists=True, integrity_error_constraint=None):
        self.wechat_group_id = "wxgroup-1"
        self.group_id = "group-uuid-1"
        self.group_active = group_active
        self.existing_member_role = existing_member_role
        self.pending_role_exists = pending_role_exists
        self._pending_integrity_error = integrity_error_constraint
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0

    def query(self, model):
        return _Query(self, model)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1
        if self._pending_integrity_error is not None:
            constraint = self._pending_integrity_error
            self._pending_integrity_error = None
            raise make_integrity_error(constraint)

    def rollback(self):
        self.rollback_count += 1
