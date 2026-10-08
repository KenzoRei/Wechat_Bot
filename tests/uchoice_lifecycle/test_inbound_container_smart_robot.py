"""
柜号 / 拆柜费 on Smart Robot (plan: docs/ai-collaboration/2026-10-unpacking-fee/
plan.md rev 5, tests 20-23), offline with fake session/DB objects. The PostgreSQL tests for
the pre-AI 是/否 path are in test_inbound_container_smart_robot_db.py.
"""
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
