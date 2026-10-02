"""
Smart Robot's outbound -> upsert_address pivot
(core/workflow_engine._maybe_pivot_to_add_address) carries the outbound's
warehouse over, so the address flow doesn't ask for it again. Fake session
and database objects, no PostgreSQL.
"""
from types import SimpleNamespace

import pytest

from core import workflow_engine

_GROUP_ID = "00000000-0000-0000-0000-000000000001"
_OUTBOUND_ID = "00000000-0000-0000-0000-000000000002"
_ADDRESS_ID = "00000000-0000-0000-0000-000000000003"

_SERVICES = [
    {"name": "uchoice_outbound_request", "service_type_id": _OUTBOUND_ID},
    {"name": "upsert_address", "service_type_id": _ADDRESS_ID},
]


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
    """Runs the pivot against an outbound draft; returns the new address session."""
    def run(draft_fields, extracted_fields):
        outbound = _FakeSession(_OUTBOUND_ID, draft_fields)
        address = _FakeSession(_ADDRESS_ID)
        monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: outbound)
        monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: address)
        monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda *a, **k: None)
        monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)
        monkeypatch.setattr(
            workflow_engine.session_manager, "update_collected_fields",
            lambda db, session, fields: setattr(session, "collected_fields", {**session.collected_fields, **fields}),
        )
        monkeypatch.setattr(workflow_engine.request_logger, "create_log",
                            lambda *a, **k: SimpleNamespace(log_id="log", serial_number="REQ-1"))
        monkeypatch.setattr(workflow_engine, "send_message", lambda *a, **k: None)

        context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "发到新地址",
                   "msg_id": "m1", "allowed_services": _SERVICES}
        ai = SimpleNamespace(
            unmatched_new_address={"company_name": "ABC", "addr": "1 Main St, Jamaica, NY 11434"},
            service_type_name="uchoice_outbound_request",
            extracted_fields=extracted_fields,
            reply="新地址，已转为新增地址流程",
        )
        assert workflow_engine._maybe_pivot_to_add_address(context, ai, _FakeDB()) is True
        return address.collected_fields
    return run


def test_warehouse_from_the_outbound_draft_is_carried_over(pivot):
    fields = pivot({"warehouse_code": "NJ"}, {})
    assert fields == {"company_name": "ABC", "addr": "1 Main St, Jamaica, NY 11434", "warehouse_code": "NJ"}


def test_warehouse_stated_in_the_pivoting_message_wins(pivot):
    assert pivot({"warehouse_code": "NJ"}, {"warehouse_code": "DE"})["warehouse_code"] == "DE"


@pytest.mark.parametrize("draft, extracted", [({}, {}), ({"warehouse_code": "XYZ"}, {})])
def test_missing_or_unknown_warehouse_is_left_for_the_address_flow_to_ask(pivot, draft, extracted):
    assert "warehouse_code" not in pivot(draft, extracted)
