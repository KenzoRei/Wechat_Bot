"""
Cancelling an in-process request now names which one -- the serial number,
same identifier shown everywhere else in this system. But only when the
session genuinely owns the log being closed: for a targets_existing_request
service (e.g. abandoning a confirm_inbound_completion attempt), the log is
the ORIGINAL request, untouched by this cancellation, so showing its serial
number would falsely imply that original request was cancelled.
"""
from types import SimpleNamespace

from core.kefu_turn_apply import cancel_kefu_turn


def _db(log):
    return SimpleNamespace(get=lambda model, key: log)


def _session(status="pending_confirmation"):
    return SimpleNamespace(
        session_id="session-1", request_log_id="log-1", service_type_id="service-1",
        status=status, conversation_history=[], collected_fields={}, customer_id=None,
    )


def test_cancel_shows_serial_number_for_an_owned_log():
    session = _session()
    log = SimpleNamespace(serial_number="REQ-1", status="pending")
    service = {"name": "uchoice_outbound_request", "targets_existing_request": False}
    reply = cancel_kefu_turn(_db(log), {"content": "取消"}, service, session)
    assert "出库申请已取消（REQ-1），您可以随时发起新申请。" == reply
    assert log.status == "cancelled"
    assert session.status == "cancelled"


def test_cancel_omits_serial_number_for_a_referenced_not_owned_log():
    session = _session()
    log = SimpleNamespace(serial_number="REQ-ORIGINAL", status="processing")
    service = {"name": "confirm_inbound_completion", "targets_existing_request": True}
    reply = cancel_kefu_turn(_db(log), {"content": "取消"}, service, session)
    assert reply == "入库完成确认已取消，您可以随时发起新申请。"
    assert "REQ-ORIGINAL" not in reply
    assert log.status == "processing"  # untouched -- not owned by this session
    assert session.status == "cancelled"


def test_cancel_with_no_session_falls_back_to_bare_message():
    reply = cancel_kefu_turn(None, {"content": "取消"}, None, None)
    assert reply == "该申请已取消，您可以随时发起新申请。"
