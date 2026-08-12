"""
Live gap: confirm_inbound_completion/confirm_outbound_completion with
multiple pending requests just asked for a bare "请提供要处理的申请编号"
with no listing -- the AI's own candidate-listing instructions live
entirely in its `reply` field, which Kefu never sends. These test the
deterministic replacement, core.kefu_turn_apply._resolve_reference_serial,
directly (pure function, no DB).
"""
from types import SimpleNamespace

from core.kefu_turn_apply import _resolve_reference_serial


def _session(collected_fields=None):
    return SimpleNamespace(collected_fields=collected_fields or {})


def _service(name="confirm_inbound_completion"):
    return {"name": name}


def test_already_resolved_reference_serial_is_left_alone():
    session = _session({"reference_serial": "REQ-1"})
    context = {"uchoice_candidates": {"pending_inbound_requests": [
        {"serial_number": "REQ-1"}, {"serial_number": "REQ-2"},
    ]}}
    assert _resolve_reference_serial(context, session, _service()) is None
    assert session.collected_fields["reference_serial"] == "REQ-1"


def test_no_pending_candidates_returns_none_eligible_message():
    session = _session()
    context = {"uchoice_candidates": {"pending_inbound_requests": []}}
    reply = _resolve_reference_serial(context, session, _service())
    assert reply is not None
    assert "没有待处理的入库申请" in reply


def test_single_candidate_auto_fills_deterministically():
    session = _session()
    context = {"uchoice_candidates": {"pending_inbound_requests": [
        {"serial_number": "REQ-20260812-001179", "warehouse_code": "JFK", "sku_summary": "S2 x10托"},
    ]}}
    reply = _resolve_reference_serial(context, session, _service())
    assert reply is None
    assert session.collected_fields["reference_serial"] == "REQ-20260812-001179"
    assert context["collected_fields"]["reference_serial"] == "REQ-20260812-001179"


def test_multiple_candidates_lists_every_option_with_identifying_info():
    session = _session()
    context = {"uchoice_candidates": {"pending_inbound_requests": [
        {"serial_number": "REQ-A", "warehouse_code": "JFK", "sku_summary": "S2 x10托"},
        {"serial_number": "REQ-B", "warehouse_code": "DE", "sku_summary": "T2 x4托"},
    ]}}
    reply = _resolve_reference_serial(context, session, _service())
    assert reply is not None
    assert "REQ-A" in reply and "JFK" in reply and "S2 x10托" in reply
    assert "REQ-B" in reply and "DE" in reply and "T2 x4托" in reply
    # session must NOT be mutated when ambiguous -- staff still has to answer
    assert "reference_serial" not in session.collected_fields


def test_outbound_uses_its_own_candidate_key_and_label():
    session = _session()
    context = {"uchoice_candidates": {"pending_outbound_requests": [
        {"serial_number": "REQ-C", "warehouse_code": "JFK", "sku_summary": "S1 x2托", "destination": "ABC Corp"},
        {"serial_number": "REQ-D", "warehouse_code": "JFK", "sku_summary": "S1 x1托", "destination": "XYZ Corp"},
    ]}}
    reply = _resolve_reference_serial(context, session, _service("confirm_outbound_completion"))
    assert reply is not None
    assert "出库申请" in reply
    assert "REQ-C" in reply and "发往ABC Corp" in reply
    assert "REQ-D" in reply and "发往XYZ Corp" in reply


def test_non_targets_existing_request_service_is_a_no_op():
    session = _session()
    context = {"uchoice_candidates": {}}
    assert _resolve_reference_serial(context, session, _service("uchoice_inbound_request")) is None
