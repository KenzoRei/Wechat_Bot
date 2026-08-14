"""
Verifies api/webhook.py wires self-registration branches in the required order:
  1. registration command recognized before access_control.check_access;
  2. pending short circuit fires after check_access but before
     resolve_session / build_context / ai_chain.process.
Mocks every collaborator at the module boundary -- no real DB, no real
webhook, no operational client calls.
"""
from types import SimpleNamespace

import pytest

import api.webhook as webhook_module
from core import access_control


class _FakeDB:
    def close(self):
        pass


def _message(**overrides):
    base = {
        "msg_type": "text",
        "chat_type": "group",
        "group_id": "wxgroup-1",
        "from_user": "user-1",
        "content": "hello",
        "response_url": "https://example.invalid/reply",
        "msg_id": "m1",
    }
    base.update(overrides)
    return base


def test_registration_command_short_circuits_before_check_access(monkeypatch):
    monkeypatch.setattr(webhook_module, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        webhook_module.self_registration, "try_handle_registration_command",
        lambda db, message: "registered!",
    )

    def _boom_check_access(*_a, **_kw):
        raise AssertionError("check_access must not be called when the registration command matched")

    monkeypatch.setattr(webhook_module.access_control, "check_access", _boom_check_access)

    sent = []
    monkeypatch.setattr(webhook_module, "send_message", lambda openid, content, response_url="": sent.append((openid, content)))

    webhook_module._process_message(_message(content="注册成员"))

    assert sent == [("user-1", "registered!")]


def test_pending_short_circuit_fires_before_session_and_ai(monkeypatch):
    monkeypatch.setattr(webhook_module, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        webhook_module.self_registration, "try_handle_registration_command",
        lambda db, message: None,  # not the registration command this turn
    )

    fake_result = access_control.AccessResult(
        wechat_openid="user-1", group_id="g1", role="pending", role_id="r1",
        display_name=None, warehouse_code=None, allowed_services=[],
        group_context=None, group_description=None,
    )
    monkeypatch.setattr(webhook_module.access_control, "check_access", lambda *_a, **_kw: fake_result)

    def _boom(*_a, **_kw):
        raise AssertionError("session/context/AI must not run for a pending member's non-command message")

    monkeypatch.setattr(webhook_module.session_manager, "resolve_session", _boom)
    monkeypatch.setattr(webhook_module.session_manager, "build_context", _boom)
    monkeypatch.setattr(webhook_module.ai_chain, "process", _boom)

    sent = []
    monkeypatch.setattr(webhook_module, "send_message", lambda openid, content, response_url="": sent.append((openid, content)))

    webhook_module._process_message(_message(content="随便问点什么"))

    assert sent == [("user-1", webhook_module.self_registration.PENDING_SHORT_CIRCUIT_REPLY)]


def test_operational_member_reaches_session_and_ai(monkeypatch):
    monkeypatch.setattr(webhook_module, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        webhook_module.self_registration, "try_handle_registration_command",
        lambda db, message: None,
    )

    fake_result = access_control.AccessResult(
        wechat_openid="user-1", group_id="g1", role="admin", role_id="r1",
        display_name=None, warehouse_code=None, allowed_services=[],
        group_context=None, group_description=None,
    )
    monkeypatch.setattr(webhook_module.access_control, "check_access", lambda *_a, **_kw: fake_result)

    calls = []
    monkeypatch.setattr(webhook_module.session_manager, "resolve_session", lambda *_a, **_kw: calls.append("resolve_session") or SimpleNamespace())
    monkeypatch.setattr(webhook_module.session_manager, "build_context", lambda *_a, **_kw: calls.append("build_context") or {})
    monkeypatch.setattr(webhook_module.ai_chain, "process", lambda *_a, **_kw: calls.append("ai_chain.process") or SimpleNamespace(intent="x", service_type_name="s", reply="ok", extracted_fields={}))
    monkeypatch.setattr(webhook_module.workflow_engine, "run_and_get_reply", lambda *_a, **_kw: calls.append("run_and_get_reply") or "done")

    webhook_module._process_message(_message(content="随便问点什么"))

    assert calls == ["resolve_session", "build_context", "ai_chain.process", "run_and_get_reply"]
