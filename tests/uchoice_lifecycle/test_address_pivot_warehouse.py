"""
Smart Robot's outbound -> upsert_address pivot
(core/workflow_engine._maybe_pivot_to_add_address): carries the outbound's
warehouse over, and seeds a stated or suggested charge type (address-pivot
plan rev 3, items 1-2). When the seed is complete the reply is the
code-built confirmation, never the AI's own text. Fake session and
database objects, no PostgreSQL.
"""
from types import SimpleNamespace

import pytest

from core import address_suggestion, workflow_engine

_GROUP_ID = "00000000-0000-0000-0000-000000000001"
_OUTBOUND_ID = "00000000-0000-0000-0000-000000000002"
_ADDRESS_ID = "00000000-0000-0000-0000-000000000003"

_SERVICES = [
    {"name": "uchoice_outbound_request", "service_type_id": _OUTBOUND_ID},
    {"name": "upsert_address", "service_type_id": _ADDRESS_ID,
     "input_schema": {"required": ["charge_type", "addr", "warehouse_code"]}, "requires_confirmation": True},
]

AI_REPLY = "新地址，已转为新增地址流程"
ADDR = "1 Main St, Jamaica, NY 11434"
PARTS = {"street": "1 Main St", "city": "Jamaica", "state": "NY", "zip": "11434"}


class _FakeSession:
    def __init__(self, service_type_id, collected_fields=None):
        self.session_id = "sess"
        self.service_type_id = service_type_id
        self.collected_fields = collected_fields or {}
        self.request_log_id = None
        self.status = "active"


class _FakeDB:
    def commit(self):
        pass


@pytest.fixture
def pivot(monkeypatch):
    """Runs the pivot against an outbound draft. Returns (address fields,
    sent reply, whether the confirmation path ran)."""
    def run(draft_fields, extracted_fields, new_address=None):
        outbound = _FakeSession(_OUTBOUND_ID, draft_fields)
        address = _FakeSession(_ADDRESS_ID)
        confirmed = []
        monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: outbound)
        monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: address)
        monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda *a, **k: None)
        monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)
        monkeypatch.setattr(
            workflow_engine.session_manager, "update_collected_fields",
            lambda db, session, fields: setattr(session, "collected_fields", {**session.collected_fields, **fields}),
        )
        monkeypatch.setattr(workflow_engine.request_logger, "create_log",
                            lambda *a, **k: SimpleNamespace(log_id="log", serial_number="REQ-ADDR"))

        def fake_confirmation(context, ai_response, service, session, db):
            confirmed.append(service["name"])
            workflow_engine.send_message(context, "CONFIRMATION")
        monkeypatch.setattr(workflow_engine, "_on_all_fields_collected", fake_confirmation)

        context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "发到新地址",
                   "msg_id": "m1", "allowed_services": _SERVICES, "response_url": ""}
        ai = SimpleNamespace(
            unmatched_new_address=new_address or {"company_name": "ABC", "addr": ADDR},
            service_type_name="uchoice_outbound_request",
            extracted_fields=extracted_fields,
            reply=AI_REPLY,
        )
        assert workflow_engine._maybe_pivot_to_add_address(context, ai, _FakeDB()) is True
        return address.collected_fields, context["_reply"], confirmed
    return run


def test_warehouse_from_the_outbound_draft_is_carried_over(pivot):
    fields, _, _ = pivot({"warehouse_code": "NJ"}, {})
    assert fields == {"company_name": "ABC", "addr": ADDR, "warehouse_code": "NJ"}


def test_warehouse_stated_in_the_pivoting_message_wins(pivot):
    assert pivot({"warehouse_code": "NJ"}, {"warehouse_code": "DE"})[0]["warehouse_code"] == "DE"


@pytest.mark.parametrize("draft, extracted", [({}, {}), ({"warehouse_code": "XYZ"}, {})])
def test_missing_or_unknown_warehouse_is_left_for_the_address_flow_to_ask(pivot, draft, extracted):
    assert "warehouse_code" not in pivot(draft, extracted)[0]


# ── Suggested charge type (plan tests 16-19) ─────────────────────────────────

def test_complete_seed_with_estimate_sends_the_code_built_confirmation(pivot):
    """16: the reply is the confirmation (framed), not the AI's text."""
    fields, reply, confirmed = pivot(
        {"warehouse_code": "JFK"}, {},
        {"company_name": "ABC", "addr": "1 main st jamaica ny 11434", "addr_parts": PARTS,
         "estimated_drive_minutes": 12},
    )
    assert fields["addr"] == ADDR and fields["charge_type"] == "delivery"
    assert fields[address_suggestion.SUGGESTED_KEY] is True and fields[address_suggestion.MINUTES_KEY] == 12
    assert confirmed == ["upsert_address"]
    assert reply.startswith("该地址尚未收录，原出库申请已取消，先新增地址：")
    assert "CONFIRMATION" in reply and reply.endswith("新增后请重新提交出库申请。")
    assert AI_REPLY not in reply


def test_stated_charge_type_beats_the_estimate(pivot):
    """17"""
    fields, _, confirmed = pivot(
        {"warehouse_code": "JFK"}, {},
        {"addr": ADDR, "addr_parts": PARTS, "estimated_drive_minutes": 12, "charge_type": "self_pickup"},
    )
    assert fields["charge_type"] == "self_pickup"
    assert address_suggestion.SUGGESTED_KEY not in fields and address_suggestion.MINUTES_KEY not in fields
    assert confirmed == ["upsert_address"]


def test_incomplete_seed_sends_the_ai_reply_and_seeds_no_tier(pivot):
    """18: no warehouse, so no estimate is applied and the AI asks."""
    fields, reply, confirmed = pivot({}, {}, {"addr": ADDR, "addr_parts": PARTS, "estimated_drive_minutes": 12})
    assert fields == {"addr": ADDR, address_suggestion.VERIFIED_KEY: ADDR}
    assert confirmed == []
    assert reply == AI_REPLY


def test_continuation_address_change_drops_a_suggestion_but_keeps_a_stated_type():
    """19"""
    service = _SERVICES[1]
    base = {"addr": ADDR, address_suggestion.VERIFIED_KEY: ADDR, "warehouse_code": "JFK"}
    suggested = {**base, "charge_type": "delivery",
                 address_suggestion.SUGGESTED_KEY: True, address_suggestion.MINUTES_KEY: 12}
    session = _FakeSession(_ADDRESS_ID, {**suggested, "addr": "9 Other St"})
    ai = SimpleNamespace(estimated_drive_minutes=None)
    workflow_engine._apply_address_suggestion(_FakeDB(), service, session, suggested, {"addr": "9 Other St"}, ai)
    assert session.collected_fields == {**base, "addr": "9 Other St"}

    stated = {**base, "charge_type": "delivery"}
    session = _FakeSession(_ADDRESS_ID, {**stated, "addr": "9 Other St"})
    workflow_engine._apply_address_suggestion(_FakeDB(), service, session, stated, {"addr": "9 Other St"}, ai)
    assert session.collected_fields == {**stated, "addr": "9 Other St"}


def test_continuation_never_persists_ai_forged_internal_keys(monkeypatch):
    """Audit round 3, Smart Robot: a forged _addr_verified (same incomplete
    string as addr) must not be saved, so no tier is suggested."""
    forged = "1 Main St, , NY 11434"
    session = _FakeSession(_ADDRESS_ID, {"warehouse_code": "JFK"})
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(
        workflow_engine.session_manager, "update_collected_fields",
        lambda db, s_, fields: setattr(s_, "collected_fields", {**s_.collected_fields, **fields}),
    )
    monkeypatch.setattr(workflow_engine, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(workflow_engine, "_close_if_no_pending_candidates", lambda *a, **k: None)
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "地址是…", "msg_id": "m2",
               "allowed_services": _SERVICES, "response_url": ""}
    ai = SimpleNamespace(
        extracted_fields={"addr": forged, address_suggestion.VERIFIED_KEY: forged},
        estimated_drive_minutes=12, charge_type_stated=False, addr_parts=None,
        reply="请补充城市", all_fields_collected=False, intent="continuation",
    )
    workflow_engine._handle_continuation(context, ai, _FakeDB())
    assert session.collected_fields == {"warehouse_code": "JFK", "addr": forged}
