"""Isolated regressions for the signed Smart Robot/Kefu parity plan."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai.base import AIResponse
from core import workflow_engine


def _ai(intent, fields, claimed, service_name=None):
    return AIResponse(intent=intent, reply="need more", extracted_fields=fields,
                      all_fields_collected=claimed,
                      service_type_name=service_name)


def _service(name, required, *, targets=False):
    return {
        "name": name, "service_type_id": str(uuid4()),
        "workflow_id": str(uuid4()), "input_schema": {"required": required},
        "targets_existing_request": targets, "requires_confirmation": True,
    }


class _DB:
    def __init__(self, log=None):
        self.log = log
        self.commits = 0

    def commit(self):
        self.commits += 1

    def query(self, _model):
        log = self.log
        return SimpleNamespace(filter_by=lambda **_kw:
                               SimpleNamespace(first=lambda: log))


def _patch_new_request(monkeypatch, service, session, progressed):
    monkeypatch.setattr(workflow_engine.session_manager, "create_session",
                        lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.session_manager, "update_collected_fields",
                        lambda _db, s, fields: setattr(s, "collected_fields",
                                                      {**s.collected_fields, **fields}))
    monkeypatch.setattr(workflow_engine.request_logger, "create_log",
                        lambda *a, **k: SimpleNamespace(log_id="log-1",
                                                       serial_number="REQ-1",
                                                       origin_session_id=None))
    monkeypatch.setattr(workflow_engine, "_sanitize_extracted_fields_before_persistence",
                        lambda _name, fields, _db, _gid=None: fields)
    monkeypatch.setattr(workflow_engine, "_autoresolve_single_candidate",
                        lambda *a: False)
    monkeypatch.setattr(workflow_engine, "_on_all_fields_collected",
                        lambda *a: progressed.append(True))
    monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a: None)
    monkeypatch.setattr(workflow_engine, "send_message", lambda *a: None)
    monkeypatch.setattr(workflow_engine, "_close_if_no_pending_candidates", lambda *a: False)


@pytest.mark.parametrize("name", ["fedex_label", "ups_label"])
def test_carrier_new_request_uses_schema_not_model_claim(monkeypatch, name):
    required = [f"field_{i}" for i in range(13)]
    service = _service(name, required)
    progressed = []
    session = SimpleNamespace(session_id=uuid4(), service_type_id=service["service_type_id"],
                              request_log_id=None, collected_fields={})
    _patch_new_request(monkeypatch, service, session, progressed)
    context = {"wechat_openid": "u", "group_id": str(uuid4()), "content": "x",
               "msg_id": None, "allowed_services": [service]}

    complete = {field: "value" for field in required}
    workflow_engine._handle_new_request(context, _ai("new_request", complete, False, name), _DB())
    assert progressed == [True], "a stale false must not strand a complete carrier request"


def test_new_request_stale_true_cannot_bypass_missing_persisted_field(monkeypatch):
    service = _service("adjust_storage", ["warehouse_code", "adjustment_lines"])
    progressed = []
    session = SimpleNamespace(session_id=uuid4(), service_type_id=service["service_type_id"],
                              request_log_id=None, collected_fields={})
    _patch_new_request(monkeypatch, service, session, progressed)
    context = {"wechat_openid": "u", "group_id": str(uuid4()), "content": "x",
               "msg_id": None, "allowed_services": [service]}

    workflow_engine._handle_new_request(
        context, _ai("new_request", {"warehouse_code": "JFK"}, True, service["name"]), _DB())
    assert progressed == []
    assert session.collected_fields == {"warehouse_code": "JFK"}


@pytest.mark.parametrize(
    "required,fields,claimed,expected",
    [([], {}, False, True),
     (["warehouse_code", "move_lines"], {"warehouse_code": "JFK"}, True, False),
     (["warehouse_code", "move_lines"], {"warehouse_code": "JFK", "move_lines": [{}]}, False, True)],
)
def test_continuation_readiness_uses_persisted_schema(monkeypatch, required, fields, claimed, expected):
    service = _service("view_storage" if not required else "move_storage", required)
    session = SimpleNamespace(session_id=uuid4(), service_type_id=service["service_type_id"],
                              request_log_id=None, collected_fields={})
    progressed = []
    monkeypatch.setattr(workflow_engine, "_get_session", lambda *a: session)
    monkeypatch.setattr(workflow_engine, "_sanitize_extracted_fields_before_persistence",
                        lambda _name, value, _db, _gid=None: value)
    monkeypatch.setattr(workflow_engine.session_manager, "update_collected_fields",
                        lambda _db, s, value: setattr(s, "collected_fields",
                                                     {**s.collected_fields, **value}))
    monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a: None)
    monkeypatch.setattr(workflow_engine, "_autoresolve_single_candidate", lambda *a: False)
    monkeypatch.setattr(workflow_engine, "_on_all_fields_collected",
                        lambda *a: progressed.append(True))
    monkeypatch.setattr(workflow_engine, "send_message", lambda *a: None)
    monkeypatch.setattr(workflow_engine, "_close_if_no_pending_candidates", lambda *a: False)
    context = {"session_id": str(session.session_id), "content": "x", "group_id": str(uuid4()),
               "allowed_services": [service]}

    workflow_engine._handle_continuation(context, _ai("continuation", fields, claimed), _DB())
    assert bool(progressed) is expected


def test_cancel_names_only_owned_request_log(monkeypatch):
    owned = _service("fedex_label", ["x"])
    referenced = _service("confirm_inbound_completion", ["reference_serial"], targets=True)
    sent = []
    monkeypatch.setattr(workflow_engine, "send_message", lambda _ctx, text: sent.append(text))
    monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda *a, **k: None)
    monkeypatch.setattr(workflow_engine.request_logger, "mark_cancelled", lambda *a, **k: None)

    for service, should_name in ((owned, True), (referenced, False)):
        session = SimpleNamespace(session_id=uuid4(), service_type_id=service["service_type_id"],
                                  request_log_id="log-1")
        monkeypatch.setattr(workflow_engine, "_get_session", lambda *a, s=session: s)
        workflow_engine._handle_cancel({"allowed_services": [service]},
                                       _DB(SimpleNamespace(serial_number="REQ-SMART-1")))
        assert ("REQ-SMART-1" in sent[-1]) is should_name


def test_cancel_without_session_keeps_bare_fallback(monkeypatch):
    sent = []
    monkeypatch.setattr(workflow_engine, "_get_session", lambda *a: None)
    monkeypatch.setattr(workflow_engine, "send_message", lambda _ctx, text: sent.append(text))
    workflow_engine._handle_cancel({}, _DB())
    assert sent == ["已取消，您可以随时发起新申请。"]
