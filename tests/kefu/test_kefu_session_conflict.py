"""
_detect_session_conflict is a pure function of (context, ai_response,
session) -- no DB access, no persisted state. These tests exercise it
directly, matching tests/kefu/test_kefu_turn_state_machine.py's
SimpleNamespace-fixture style.
"""
from types import SimpleNamespace

from core.kefu_case_adapter import _detect_session_conflict


_OUTBOUND_ID = "22222222-0000-0000-0000-000000000002"
_INBOUND_ID = "11111111-0000-0000-0000-000000000001"

_ALLOWED_SERVICES = [
    {"service_type_id": _OUTBOUND_ID, "name": "uchoice_outbound_request"},
    {"service_type_id": _INBOUND_ID, "name": "uchoice_inbound_request"},
    {"service_type_id": "33333333-0000-0000-0000-000000000003", "name": "view_storage"},
]


def _session(status="active", service_type_id=_OUTBOUND_ID, history=None):
    return SimpleNamespace(
        status=status,
        service_type_id=service_type_id,
        conversation_history=history or [],
        case_number="CASE-1",
    )


def _ai(intent="new_request", service_type_name=None):
    return SimpleNamespace(intent=intent, service_type_name=service_type_name)


def _context():
    return {"allowed_services": _ALLOWED_SERVICES}


def test_no_open_session_never_conflicts():
    ai = _ai(service_type_name="uchoice_inbound_request")
    assert _detect_session_conflict(_context(), ai, None) is None


def test_closed_session_never_conflicts():
    session = _session(status="cancelled")
    ai = _ai(service_type_name="uchoice_inbound_request")
    assert _detect_session_conflict(_context(), ai, session) is None


def test_continuation_intent_never_conflicts():
    session = _session()
    ai = _ai(intent="continuation", service_type_name=None)
    assert _detect_session_conflict(_context(), ai, session) is None


def test_read_only_service_bypasses_conflict():
    session = _session()
    ai = _ai(service_type_name="view_storage")
    assert _detect_session_conflict(_context(), ai, session) is None


def test_same_service_never_conflicts():
    session = _session(service_type_id=_OUTBOUND_ID)
    ai = _ai(service_type_name="uchoice_outbound_request")
    assert _detect_session_conflict(_context(), ai, session) is None


def test_distinct_mutating_service_triggers_conflict_with_last_question():
    session = _session(service_type_id=_OUTBOUND_ID, history=[
        {"role": "user", "content": "帮我出库"},
        {"role": "assistant", "content": "请提供目的地地址。"},
    ])
    ai = _ai(service_type_name="uchoice_inbound_request")
    reply = _detect_session_conflict(_context(), ai, session)
    assert reply is not None
    assert "出库申请" in reply
    assert "CASE-1" in reply
    assert "请提供目的地地址。" in reply
    assert "取消" in reply and "继续" in reply


def test_conflict_reply_omits_reference_line_when_no_prior_assistant_turn():
    session = _session(service_type_id=_OUTBOUND_ID, history=[
        {"role": "user", "content": "帮我出库"},
    ])
    ai = _ai(service_type_name="uchoice_inbound_request")
    reply = _detect_session_conflict(_context(), ai, session)
    assert reply is not None
    assert "上次系统询问" not in reply


def test_unresolvable_new_service_name_never_conflicts():
    session = _session()
    ai = _ai(service_type_name="not_a_real_service")
    assert _detect_session_conflict(_context(), ai, session) is None
