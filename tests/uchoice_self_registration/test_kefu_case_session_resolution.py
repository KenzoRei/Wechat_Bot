"""
core.kefu_case_adapter._resolve_kefu_session: case_number_hint priority over
the staff's current-case binding, per-turn reauthorization, and denial of
unknown, closed, or unauthorized cases. Mock DB
only; the full _process_turn path (AI chain + workflow_engine) is
intentionally NOT covered here -- see this module's own docstring note in
core/kefu_case_adapter.py documents the limited end-to-end orchestration
coverage.
"""
from types import SimpleNamespace

from core.kefu_case_adapter import _resolve_kefu_session
from core.kefu_contracts import CaseTurnDenied
from models.kefu import KefuStaffCaseContext
from models.session import ConversationSession


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter_by(self, **kwargs):
        case_number = kwargs.get("case_number")
        for session in self.db.sessions.values():
            if session.case_number == case_number:
                return _First(session)
        return _First(None)


class _First:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class MockDB:
    def __init__(self, sessions=None, bindings=None):
        self.sessions = sessions or {}
        self.bindings = bindings or {}

    def query(self, model):
        return _Query(self, model)

    def get(self, model, pk):
        if model is ConversationSession:
            return self.sessions.get(pk)
        if model is KefuStaffCaseContext:
            return self.bindings.get(pk)
        return None


GROUP_A = "group-a"
GROUP_B = "group-b"
SVC_INBOUND = "svc-inbound"
SVC_OUTBOUND = "svc-outbound"


def _session(session_id, *, status="active", case_number=None, revision=0,
             group_id=GROUP_A, service_type_id=None, collected_fields=None):
    return SimpleNamespace(
        session_id=session_id, status=status, case_number=case_number, case_revision=revision,
        group_id=group_id, service_type_id=service_type_id, collected_fields=collected_fields or {},
    )


def _access(staff_id="staff-1", group_id=GROUP_A, warehouse_code=None, allowed_services=None):
    return SimpleNamespace(
        staff_id=staff_id, group_id=group_id, warehouse_code=warehouse_code,
        allowed_services=allowed_services or [{"service_type_id": SVC_INBOUND}, {"service_type_id": SVC_OUTBOUND}],
    )


def test_no_hint_no_binding_is_a_brand_new_case():
    db = MockDB()
    assert _resolve_kefu_session(db, _access(), None) is None


def test_hint_resolves_to_live_authorized_session():
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001", service_type_id=SVC_INBOUND)
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(), "CASE-20260811-000001")
    assert result is s


def test_hint_to_unknown_case_is_denied_not_stale():
    db = MockDB()
    result = _resolve_kefu_session(db, _access(), "CASE-20260811-999999")
    assert isinstance(result, CaseTurnDenied)
    assert result.reason == "case_not_found"


def test_hint_to_closed_case_is_denied():
    s = _session("sess-1", status="completed", case_number="CASE-20260811-000001")
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(), "CASE-20260811-000001")
    assert isinstance(result, CaseTurnDenied)
    assert result.reason == "case_closed"


def test_hint_to_wrong_group_case_is_denied():
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001", group_id=GROUP_B)
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(group_id=GROUP_A), "CASE-20260811-000001")
    assert isinstance(result, CaseTurnDenied)
    assert result.reason == "case_wrong_group"


def test_hint_to_case_whose_service_is_not_granted_is_denied():
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001", service_type_id="svc-role-change")
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(), "CASE-20260811-000001")
    assert isinstance(result, CaseTurnDenied)
    assert result.reason == "case_service_not_granted"


def test_hint_to_case_in_different_warehouse_is_denied():
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001",
                 service_type_id=SVC_INBOUND, collected_fields={"warehouse_code": "DE"})
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(warehouse_code="JFK"), "CASE-20260811-000001")
    assert isinstance(result, CaseTurnDenied)
    assert result.reason == "case_wrong_warehouse"


def test_hint_to_case_with_matching_warehouse_is_authorized():
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001",
                 service_type_id=SVC_INBOUND, collected_fields={"warehouse_code": "JFK"})
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(warehouse_code="JFK"), "CASE-20260811-000001")
    assert result is s


def test_unscoped_staff_not_denied_by_warehouse():
    """admin/accountant have no warehouse_code -- not warehouse-scoped."""
    s = _session("sess-1", status="active", case_number="CASE-20260811-000001",
                 service_type_id=SVC_INBOUND, collected_fields={"warehouse_code": "DE"})
    db = MockDB(sessions={"sess-1": s})

    result = _resolve_kefu_session(db, _access(warehouse_code=None), "CASE-20260811-000001")
    assert result is s


def test_hint_takes_priority_over_staff_binding():
    hinted = _session("sess-hinted", status="active", case_number="CASE-20260811-000002")
    bound = _session("sess-bound", status="active", case_number="CASE-20260811-000003")
    db = MockDB(
        sessions={"sess-hinted": hinted, "sess-bound": bound},
        bindings={"staff-1": SimpleNamespace(staff_id="staff-1", active_session_id="sess-bound")},
    )

    result = _resolve_kefu_session(db, _access(), "CASE-20260811-000002")
    assert result is hinted


def test_no_hint_falls_back_to_authorized_staff_binding():
    bound = _session("sess-bound", status="pending_confirmation", case_number="CASE-20260811-000003")
    db = MockDB(
        sessions={"sess-bound": bound},
        bindings={"staff-1": SimpleNamespace(staff_id="staff-1", active_session_id="sess-bound")},
    )

    result = _resolve_kefu_session(db, _access(), None)
    assert result is bound


def test_binding_to_closed_session_is_treated_as_new_case():
    bound = _session("sess-bound", status="cancelled", case_number="CASE-20260811-000003")
    db = MockDB(
        sessions={"sess-bound": bound},
        bindings={"staff-1": SimpleNamespace(staff_id="staff-1", active_session_id="sess-bound")},
    )

    result = _resolve_kefu_session(db, _access(), None)
    assert result is None


def test_binding_with_no_active_session_id_is_new_case():
    db = MockDB(bindings={"staff-1": SimpleNamespace(staff_id="staff-1", active_session_id=None)})
    result = _resolve_kefu_session(db, _access(), None)
    assert result is None


def test_binding_to_now_unauthorized_case_silently_falls_back_to_new_case():
    """
    Unlike an explicit hint, the staff never asked for this specific case
    by name -- an implicit binding gone stale (e.g. their warehouse
    changed) silently starts a fresh case rather than blocking the turn.
    """
    bound = _session("sess-bound", status="active", case_number="CASE-20260811-000003", group_id=GROUP_B)
    db = MockDB(
        sessions={"sess-bound": bound},
        bindings={"staff-1": SimpleNamespace(staff_id="staff-1", active_session_id="sess-bound")},
    )

    result = _resolve_kefu_session(db, _access(group_id=GROUP_A), None)
    assert result is None
