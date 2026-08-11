"""Mock DB for core.kefu_registration tests -- mirrors _mock_db.py's exact
pattern, against KefuStaff instead of GroupMember."""

from types import SimpleNamespace

from models.kefu import KefuStaff
from models.role import Role
from tests.uchoice_self_registration._mock_db import make_integrity_error


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def first(self):
        if self.model is KefuStaff:
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


class KefuMockDB:
    """
    existing_member_role: None (not registered yet) or a role name string
      (e.g. "pending", "admin") for an already-registered identity.
    pending_role_exists: whether the 'pending' role row exists.
    integrity_error_constraint: if set, the first commit() call raises an
      IntegrityError with this constraint name; subsequent commits succeed.
    """

    def __init__(self, existing_member_role=None, pending_role_exists=True, integrity_error_constraint=None):
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
