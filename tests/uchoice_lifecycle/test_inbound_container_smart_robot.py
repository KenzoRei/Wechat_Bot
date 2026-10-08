"""
柜号 / 拆柜费 on Smart Robot (plan: docs/ai-collaboration/2026-10-unpacking-fee/
plan.md rev 5, tests 20-24). 20-22 use fake session/DB objects; 23-24 use
real PostgreSQL for the pre-AI 是/否 path and its commit.
"""
import uuid
from pathlib import Path

import pytest

from ai.base import AIResponse
from core import inbound_container as ic
from core import workflow_engine

_GROUP_ID = "00000000-0000-0000-0000-000000000001"
_INBOUND_ID = "00000000-0000-0000-0000-000000000021"
_INBOUND = {"name": "uchoice_inbound_request", "service_type_id": _INBOUND_ID,
            "input_schema": {"required": ["sku_lines", "container_number"]}, "requires_confirmation": True}


class _FakeSession:
    def __init__(self, fields, status="active"):
        self.session_id = "sess"
        self.service_type_id = _INBOUND_ID
        self.collected_fields = fields
        self.request_log_id = None
        self.status = status


class _FakeDB:
    def commit(self):
        pass


@pytest.fixture
def sr(monkeypatch):
    def setup(fields, status="active"):
        session = _FakeSession(fields, status)
        monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
        monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)
        monkeypatch.setattr(workflow_engine.session_manager, "update_collected_fields",
                            lambda db, s, f: setattr(s, "collected_fields", {**s.collected_fields, **f}))
        monkeypatch.setattr(workflow_engine, "_on_all_fields_collected",
                            lambda *a, **k: workflow_engine.send_message(a[0], "CONFIRMATION"))
        return session
    return setup


def _context(content):
    return {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": content, "msg_id": "m",
            "allowed_services": [_INBOUND], "response_url": "", "session_id": "sess"}


def test_non_standard_container_question_is_code_built(sr):
    """20: the AI's own reply is never sent for the double-check."""
    session = sr({"sku_lines": [{"sku_code": "s1", "boxes_per_pallet": 10, "pallet_count": 1}],
                  ic.ASKED_KEY: "container_number"})
    context = _context("XYZ123")
    ai = AIResponse(intent="continuation", extracted_fields={"container_number": "XYZ123"},
                    reply="AI WORDING", service_type_name=None, all_fields_collected=False)
    workflow_engine.run_and_get_reply(context, ai, _FakeDB())
    assert context["_reply"] == ic.container_check_question("XYZ123")
    assert session.collected_fields[ic.PENDING_KEY] == {"field": "container_number", "value": "XYZ123"}


def test_receipt_with_container_and_no_fee_is_blocked(monkeypatch):
    """21: the shared validator, which Smart Robot runs too."""
    import core.uchoice_context
    from core.pre_confirm_validators import PRE_CONFIRM_VALIDATORS, _inbound_unpacking_fee_required
    monkeypatch.setattr(core.uchoice_context, "resolve_completion_target",
                        lambda db, serial: (object(), {"container_number": "MSCU1234567"}))
    message = _inbound_unpacking_fee_required({}, {"reference_serial": "REQ-1"}, None)
    assert message == "该入库柜号 MSCU1234567，请提供拆柜费金额（美元，可为 0）。"
    assert _inbound_unpacking_fee_required({}, {"reference_serial": "REQ-1", "unpacking_fee": 0}, None) is None
    assert _inbound_unpacking_fee_required({}, {ic.INVALID_FEE_KEY: True}, None).startswith("请先提供有效的拆柜费金额")
    # Registered for the receipt service, which both channels run before confirmation.
    assert "confirm_inbound_completion" in PRE_CONFIRM_VALIDATORS


def test_ai_confirm_while_a_check_is_pending_asks_again(sr):
    """22"""
    session = sr({"sku_lines": [], ic.PENDING_KEY: {"field": "container_number", "value": "XYZ123"}})
    context = _context("确认")
    ai = AIResponse(intent="confirm", extracted_fields={}, reply="", service_type_name=None, all_fields_collected=False)
    workflow_engine.run_and_get_reply(context, ai, _FakeDB())
    assert context["_reply"] == ic.container_check_question("XYZ123")
    assert session.status == "active"


def test_webhook_answers_pending_checks_before_calling_the_ai():
    """23: the pre-AI step sits before ai_chain.process in the webhook."""
    source = (Path(__file__).resolve().parents[2] / "api" / "webhook.py").read_text(encoding="utf-8")
    assert source.index("workflow_engine.pending_value_check_reply(") < source.index("ai_chain.process(")


# ── 23-24 against PostgreSQL ─────────────────────────────────────────────────

@pytest.fixture
def robot_case():
    """A Smart Robot inbound request with an open 柜号 double-check."""
    from sqlalchemy import text
    from database import SessionLocal

    db = SessionLocal()
    created = {}
    try:
        group_id = db.execute(text("select group_id from group_config order by created_at limit 1")).scalar_one()
        service = db.execute(text(
            "select st.service_type_id, st.input_schema, gs.workflow_id from service_type st "
            "join group_service gs on gs.service_type_id = st.service_type_id and gs.group_id = :g "
            "where st.name = 'uchoice_inbound_request'"), {"g": group_id}).mappings().one()
        sku = f"uf{uuid.uuid4().hex[:8]}"
        db.execute(text("insert into uchoice_sku(sku_code, description) values (:s, 'UF test SKU')"), {"s": sku})
        openid = f"o-uf-{uuid.uuid4().hex[:8]}"
        import json
        fields = {"warehouse_code": "JFK", "sku_lines": [{"sku_code": sku, "boxes_per_pallet": 10, "pallet_count": 1}],
                  ic.PENDING_KEY: {"field": "container_number", "value": "XYZ123"}}
        session_id = db.execute(text(
            "insert into conversation_session (wechat_openid, group_id, service_type_id, status, conversation_history, "
            "collected_fields, expires_at, source_channel) values (:o, :g, :st, 'active', '[]'::jsonb, cast(:f as jsonb), "
            "now() + interval '30 minutes', 'smart_robot') returning session_id"),
            {"o": openid, "g": group_id, "st": service["service_type_id"], "f": json.dumps(fields)}).scalar_one()
        log_id = db.execute(text(
            "insert into request_log (wechat_openid, group_id, service_type_id, status, raw_message, source_channel, "
            "origin_session_id) values (:o, :g, :st, 'pending', 'uf test', 'smart_robot', :s) returning log_id"),
            {"o": openid, "g": group_id, "st": service["service_type_id"], "s": session_id}).scalar_one()
        db.execute(text("update conversation_session set request_log_id = :l where session_id = :s"),
                   {"l": log_id, "s": session_id})
        db.commit()
        created = {"session_id": session_id, "log_id": log_id, "sku": sku}

        def context(content):
            return {"wechat_openid": openid, "group_id": str(group_id), "content": content, "msg_id": uuid.uuid4().hex,
                    "response_url": "", "session_id": str(session_id), "role": "customer", "warehouse_codes": None,
                    "allowed_services": [{"name": "uchoice_inbound_request",
                                          "service_type_id": str(service["service_type_id"]),
                                          "workflow_id": str(service["workflow_id"]),
                                          "input_schema": service["input_schema"], "requires_confirmation": True,
                                          "targets_existing_request": False}]}
        yield context, session_id
    finally:
        db.rollback()
        if created:
            db.execute(text("update conversation_session set request_log_id = null where session_id = :s"),
                       {"s": created["session_id"]})
            db.execute(text("delete from request_log where log_id = :l"), {"l": created["log_id"]})
            db.execute(text("delete from conversation_session where session_id = :s"), {"s": created["session_id"]})
            db.execute(text("delete from uchoice_sku where sku_code = :k"), {"k": created["sku"]})
            db.commit()
        db.close()


def _fresh_fields(session_id):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        return db.execute(text("select status, collected_fields from conversation_session where session_id = :s"),
                          {"s": session_id}).mappings().one()
    finally:
        db.close()


def test_yes_is_saved_for_the_next_message(robot_case):
    """23 + 24 (是): handled without the AI; a fresh session sees the
    accepted value and the confirmation stage -- never execution."""
    from database import SessionLocal
    context, session_id = robot_case
    db = SessionLocal()
    try:
        reply = workflow_engine.pending_value_check_reply(context("是"), db)
    finally:
        db.close()
    row = _fresh_fields(session_id)
    assert reply is not None and "柜号：XYZ123" in reply
    assert row["collected_fields"]["container_number"] == "XYZ123"
    assert ic.PENDING_KEY not in row["collected_fields"] and row["status"] == "pending_confirmation"


def test_no_is_saved_for_the_next_message(robot_case):
    """24 (否)"""
    from database import SessionLocal
    context, session_id = robot_case
    db = SessionLocal()
    try:
        reply = workflow_engine.pending_value_check_reply(context("否"), db)
    finally:
        db.close()
    fields = _fresh_fields(session_id)["collected_fields"]
    assert reply == ic.CONTAINER_QUESTION
    assert "container_number" not in fields and ic.PENDING_KEY not in fields


def test_failure_rolls_back_and_keeps_the_check(robot_case, monkeypatch):
    """24 (error): rolled back, re-raised for the webhook's error reply."""
    from database import SessionLocal
    context, session_id = robot_case

    def boom(*a, **k):
        raise RuntimeError("forced")
    monkeypatch.setattr(workflow_engine, "_handle_continuation", boom)
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError):
            workflow_engine.pending_value_check_reply(context("是"), db)
    finally:
        db.close()
    assert _fresh_fields(session_id)["collected_fields"][ic.PENDING_KEY]["value"] == "XYZ123"


def test_other_replies_go_to_the_ai(robot_case):
    from database import SessionLocal
    context, _ = robot_case
    db = SessionLocal()
    try:
        assert workflow_engine.pending_value_check_reply(context("MSCU1234567"), db) is None
    finally:
        db.close()
