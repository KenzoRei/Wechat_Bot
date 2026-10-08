"""
柜号 / 拆柜费 on Smart Robot, against real PostgreSQL (plan:
docs/ai-collaboration/2026-10-unpacking-fee/plan.md rev 5, tests 23-24): the
pre-AI 是/否 path, its own commit, and restore on error. Listed in
tests/conftest.py's _POSTGRES_TEST_FILES so the offline CI job skips it; the
offline tests for the same feature are in test_inbound_container_smart_robot.py.
"""
import uuid

import pytest

from core import inbound_container as ic
from core import workflow_engine


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


def test_failure_after_commits_still_restores_the_check(robot_case, monkeypatch):
    """Implementation audit P2: the container step and field merge commit
    before the confirmation is rendered; a failure there must still leave
    the double-check open for the next message."""
    from database import SessionLocal
    context, session_id = robot_case

    def boom(*a, **k):
        raise RuntimeError("forced at confirmation")
    monkeypatch.setattr(workflow_engine, "_on_all_fields_collected", boom)
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError):
            workflow_engine.pending_value_check_reply(context("是"), db)
    finally:
        db.close()
    row = _fresh_fields(session_id)
    assert row["collected_fields"][ic.PENDING_KEY]["value"] == "XYZ123"
    assert "container_number" not in row["collected_fields"] and row["status"] == "active"
