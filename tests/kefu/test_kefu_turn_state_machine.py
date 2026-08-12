from types import SimpleNamespace

from core import kefu_turn_apply


class _DB:
    def __init__(self, log):
        self.log = log

    def get(self, model, key):
        return self.log


def _session(status="active"):
    return SimpleNamespace(
        session_id="session-1",
        request_log_id="log-1",
        service_type_id="service-1",
        customer_id="customer-1",
        status=status,
        case_revision=1,
        collected_fields={},
        conversation_history=[],
        updated_at=None,
        expires_at=None,
    )


def _service(**overrides):
    result = {
        "name": "uchoice_inbound_request",
        "service_type_id": "service-1",
        "workflow_id": "workflow-1",
        "input_schema": {"required": []},
        "requires_confirmation": True,
        "awaits_completion": True,
        "targets_existing_request": False,
    }
    result.update(overrides)
    return result


def test_ready_customer_request_stops_at_pending_confirmation(monkeypatch):
    # kefu-deterministic-response-plan.md correction: uchoice_inbound_request/
    # uchoice_outbound_request no longer require or resolve a customer_id --
    # every current U-Choice service is performed on behalf of U-Choice
    # itself, the sole platform tenant today (see decisions.md's
    # "Superseded or challenged assumptions").
    session = _session()
    log = SimpleNamespace(serial_number="REQ-1", customer_id=None, status="pending")
    db = _DB(log)
    ai = SimpleNamespace(extracted_fields={}, all_fields_collected=True, reply="ready")
    monkeypatch.setattr(kefu_turn_apply.pre_confirm_validators, "run", lambda *args: None)
    monkeypatch.setattr(kefu_turn_apply, "_render_confirmation", lambda *args: "CONFIRM THIS")

    reply = kefu_turn_apply.apply_kefu_turn(
        db,
        {"group_id": "group-1", "content": "request", "uchoice_candidates": {}},
        ai,
        _service(),
        session,
    )

    assert reply == "CONFIRM THIS"
    assert session.status == "pending_confirmation"
    assert log.status == "pending"
    assert session.conversation_history[-1] == {"role": "assistant", "content": "CONFIRM THIS"}


def test_confirm_transitions_processing_before_business_execution(monkeypatch):
    session = _session("pending_confirmation")
    log = SimpleNamespace(serial_number="REQ-1", status="pending")
    seen = {}

    def finish(db, context, service, current_session, current_log):
        seen["session_status"] = current_session.status
        seen["log_status"] = current_log.status
        current_session.status = "completed"
        return "done"

    monkeypatch.setattr(kefu_turn_apply, "_finish_execution", finish)
    reply = kefu_turn_apply.confirm_kefu_turn(
        _DB(log), {"content": "确认"}, _service(), session
    )
    assert reply == "done"
    assert seen == {"session_status": "active", "log_status": "processing"}


def test_duplicate_confirm_never_runs_business_execution(monkeypatch):
    session = _session("completed")
    log = SimpleNamespace(serial_number="REQ-1", status="processing")

    def forbidden(*args):
        raise AssertionError("business execution must not be repeated")

    monkeypatch.setattr(kefu_turn_apply, "_finish_execution", forbidden)
    reply = kefu_turn_apply.confirm_kefu_turn(
        _DB(log), {"content": "确认"}, _service(), session
    )
    assert "不能重复确认" in reply


def test_cancel_closes_owned_log_without_commit():
    session = _session("pending_confirmation")
    log = SimpleNamespace(serial_number="REQ-1", status="pending")
    reply = kefu_turn_apply.cancel_kefu_turn(
        _DB(log), {"content": "取消"}, _service(), session
    )
    assert "已取消" in reply
    assert session.status == "cancelled"
    assert log.status == "cancelled"


def test_stale_all_fields_collected_claim_cannot_bypass_missing_fields_check(monkeypatch):
    """
    Live incident: the AI claimed all_fields_collected=true while stuffing
    an invalid, non-list value into sku_lines ("库存" -- a bare string, not
    a proper line array). _sanitize_extracted_fields_before_persistence
    correctly drops it, but the OLD `ai_response.all_fields_collected or
    _all_required_fields_present(...)` readiness check trusted the AI's
    stale claim regardless, sailing past the missing-fields prompt straight
    into pre_confirm_validators -- which then produced the confusing
    validator-level "商品明细必须是列表" message instead of a normal
    "please provide sku_lines" prompt. The AI's raw claim must never bypass
    what's actually left in collected_fields after sanitization.
    """
    session = _session()
    log = SimpleNamespace(serial_number="REQ-1", status="pending")
    service = _service(name="uchoice_outbound_request", input_schema={"required": ["sku_lines"]})
    ai = SimpleNamespace(
        extracted_fields={"sku_lines": "库存"},  # malformed -- not a list
        all_fields_collected=True,               # AI's stale claim
        reply="ready",
    )
    monkeypatch.setattr(kefu_turn_apply.pre_confirm_validators, "run", lambda *args: None)

    reply = kefu_turn_apply.apply_kefu_turn(
        _DB(log),
        {"group_id": "group-1", "content": "库存", "uchoice_candidates": {}},
        ai,
        service,
        session,
    )

    # Must re-ask for sku_lines, never reach a validator-level error about
    # a field the user was never even told was still missing.
    assert "商品明细必须是列表" not in reply
    assert "sku_lines" not in session.collected_fields
    assert session.status == "active"
