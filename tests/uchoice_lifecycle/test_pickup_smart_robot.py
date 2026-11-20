"""
Customer pickup on the Smart Robot channel (core/workflow_engine.py wiring of
core/pickup.py) -- docs/ai-collaboration/2026-11-customer-pickup/plan.md.

Fake session objects plus a real in-memory SQLite database that holds only the
address table, so the resolver and sanitizer run for real.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import pickup, workflow_engine
from models.uchoice import UchoiceAddress

GENERAL = uuid.UUID("a1000000-0042-0000-0000-000000000001")
JFK_RET = uuid.UUID("bb8c35cb-dda1-404d-9294-682ed3df5659")
PLAIN = uuid.UUID("c0ffee00-1111-4111-8111-111111111111")
OUTBOUND = "uchoice_outbound_request"
_GROUP_ID = "00000000-0000-0000-0000-000000000001"
_SERVICE_ID = "00000000-0000-0000-0000-000000000002"


def _row(address_id, company, charge, addr, wh, kind):
    return UchoiceAddress(address_id=address_id, company_name=company, charge_type=charge, addr=addr,
                          warehouse_code=wh, created_by="test", pickup_kind=kind,
                          created_at=datetime.now(timezone.utc))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    UchoiceAddress.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        _row(JFK_RET, "JFK仓库自提留存", "self_pickup", "14502 156th St", "JFK", "retention"),
        _row(GENERAL, "客户自提", "self_pickup", "", None, "customer"),
        _row(PLAIN, "ABC Trading", "delivery", "123 Main St", "JFK", None),
    ])
    session.commit()
    yield session
    session.close()


class _FakeSession:
    def __init__(self, collected_fields=None, status="active"):
        self.session_id = "sess-1"
        self.service_type_id = _SERVICE_ID
        self.collected_fields = collected_fields or {}
        self.request_log_id = None
        self.status = status
        self.conversation_history = []


def _service():
    return {"name": OUTBOUND, "service_type_id": _SERVICE_ID, "workflow_id": _SERVICE_ID,
            "input_schema": {"required": ["sku_lines", "destination_address_id"]},
            "targets_existing_request": False, "requires_confirmation": True}


def _ai(extracted=None, unmatched=None, content_type="new_request"):
    return SimpleNamespace(all_fields_collected=False, extracted_fields=extracted or {}, reply="ai reply",
                           service_type_name=OUTBOUND, intent=content_type, unmatched_new_address=unmatched)


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(workflow_engine, "send_message", lambda ctx, text: messages.append(text))
    return messages


@pytest.fixture(autouse=True)
def _stub_session_writes(monkeypatch):
    def _update(db, session, fields):
        session.collected_fields = {**session.collected_fields, **fields}
    monkeypatch.setattr(workflow_engine.session_manager, "update_collected_fields", _update)
    monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(workflow_engine, "_on_all_fields_collected", lambda *a, **k: None)
    monkeypatch.setattr(workflow_engine, "_apply_address_suggestion", lambda *a, **k: None)
    monkeypatch.setattr(workflow_engine, "_inbound_container_step", lambda *a, **k: False)
    monkeypatch.setattr(workflow_engine, "_resolve_label_billing_customer", lambda *a, **k: False)
    monkeypatch.setattr(workflow_engine, "_autoresolve_single_candidate", lambda *a, **k: False)
    monkeypatch.setattr(workflow_engine, "_close_if_no_pending_candidates", lambda *a, **k: False)


def _new_request(monkeypatch, db, ai, content, collected=None):
    session = _FakeSession(collected_fields=collected if collected is not None else {})
    monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.request_logger, "create_log",
                        lambda *a, **k: SimpleNamespace(log_id="log-1", serial_number="REQ-1"))
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": content, "msg_id": "m1",
               "allowed_services": [_service()], "warehouse_codes": None}
    workflow_engine._handle_new_request(context, ai, db)
    return session, context


def _continuation(monkeypatch, db, ai, content, collected, status="active"):
    session = _FakeSession(collected_fields=collected, status=status)
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    context = {"session_id": "sess-1", "content": content, "allowed_services": [_service()],
               "group_id": _GROUP_ID, "warehouse_codes": None}
    workflow_engine._handle_continuation(context, ai, db)
    return session, context


# ── creation stamp + the transition step ──────────────────────────────────────

def test_a_new_outbound_request_is_stamped_at_creation(monkeypatch, db, sent):
    session, _ = _new_request(monkeypatch, db, _ai({"warehouse_code": "JFK"}), "发货")
    assert session.collected_fields[pickup.MARKER_KEY] == pickup.MARKER_VALUE


def test_initial_customer_pickup_resolves_the_row_and_keeps_the_name(monkeypatch, db, sent):
    ai = _ai({"warehouse_code": "JFK", "pickup_type": "customer", "pickup_customer_name": "王小明"})
    session, _ = _new_request(monkeypatch, db, ai, "王小明来自提 S2 3箱")
    assert session.collected_fields["destination_address_id"] == str(GENERAL)
    assert session.collected_fields["pickup_customer_name"] == "王小明"
    assert pickup.ASK_WHICH not in sent       # (the AI's own reply asks for the still-missing SKU lines)


def test_an_ai_written_pickup_row_id_never_becomes_the_destination(monkeypatch, db, sent):
    ai = _ai({"warehouse_code": "JFK", "destination_address_id": str(GENERAL)})
    session, _ = _new_request(monkeypatch, db, ai, "发货")
    assert "destination_address_id" not in session.collected_fields


def test_a_disagreement_is_answered_with_the_question_and_nothing_is_persisted(monkeypatch, db, sent):
    ai = _ai({"warehouse_code": "JFK", "pickup_type": "customer", "pickup_customer_name": "王小明"})
    session, _ = _new_request(monkeypatch, db, ai, "发给王小明 S2 3箱")
    assert sent == [pickup.ASK_WHICH]
    assert "pickup_type" not in session.collected_fields and "destination_address_id" not in session.collected_fields


def test_a_name_only_continuation_keeps_the_pickup(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "destination_address_id": str(GENERAL)}
    session, _ = _continuation(monkeypatch, db, _ai({"pickup_customer_name": "王小明"}, content_type="continuation"),
                               "王小明", collected)
    assert session.collected_fields["pickup_type"] == "customer"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)
    assert session.collected_fields["pickup_customer_name"] == "王小明"
    assert pickup.ASK_WHICH not in sent


def test_revision_to_delivery_leaves_no_stale_pickup_fields(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)}
    ai = _ai({"pickup_type": "none", "destination_address_id": str(PLAIN)}, content_type="continuation")
    session, _ = _continuation(monkeypatch, db, ai, "不是自提，送到 ABC Trading", collected)
    assert session.collected_fields["destination_address_id"] == str(PLAIN)
    assert "pickup_type" not in session.collected_fields and "pickup_customer_name" not in session.collected_fields


def test_revision_to_pickup_replaces_a_delivery_destination(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "destination_address_id": str(PLAIN)}
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"}, content_type="continuation")
    session, _ = _continuation(monkeypatch, db, ai, "这不是送货，是客户自己来提货", collected)
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


# ── clarifications must not leave a confirmation executable (audit 15-17) ─────

def test_a_clarification_takes_a_pending_confirmation_back_to_collecting(monkeypatch, db, sent):
    """The unreviewed warehouse change that came with the vague pickup must not
    be confirmable: a fresh summary is required."""
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "destination_address_id": str(PLAIN)}
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明", "warehouse_code": "DE"},
             content_type="continuation")
    session, _ = _continuation(monkeypatch, db, ai, "发给王小明", collected, status="pending_confirmation")
    assert sent == [pickup.ASK_WHICH]
    assert session.status == "active"
    assert "pickup_type" not in session.collected_fields


def test_a_repeated_pickup_type_does_not_hide_a_negation_and_asks(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)}
    ai = _ai({"pickup_type": "customer"}, content_type="continuation")
    session, _ = _continuation(monkeypatch, db, ai, "不是自提，送到 ABC Trading", collected, status="pending_confirmation")
    assert sent == [pickup.ASK_WHICH] and session.status == "active"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)      # the pickup is kept until answered


def test_a_new_name_with_retention_asks_instead_of_being_dropped(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK"}
    ai = _ai({"pickup_type": "retention", "pickup_customer_name": "张三"}, content_type="continuation")
    session, _ = _continuation(monkeypatch, db, ai, "JFK仓库自提留存，张三", collected, status="pending_confirmation")
    assert sent == [pickup.ASK_RETENTION_NAME] and session.status == "active"
    assert "pickup_type" not in session.collected_fields


# ── the address-pivot gate ────────────────────────────────────────────────────

UNMATCHED = {"company_name": "Some New Co", "addr": "9 Elm St, Queens, NY 11101"}


def _pivot(monkeypatch, db, *, session, ai, content):
    reached = []
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    monkeypatch.setattr(workflow_engine, "_find_service_by_type_id", lambda context, type_id: _service())

    def _find(context, name):
        reached.append(name)
        return None        # no add-address permission: the pivot returns False right after the gate
    monkeypatch.setattr(workflow_engine, "_find_service", _find)
    result = workflow_engine._maybe_pivot_to_add_address({"content": content}, ai, db)
    return result, reached


def test_initial_pickup_naming_an_unmatched_address_does_not_pivot(monkeypatch, db):
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"}, unmatched=UNMATCHED)
    _, reached = _pivot(monkeypatch, db, session=None, ai=ai, content="王小明来自提，他公司在 Some New Co")
    assert reached == []


def test_name_only_continuation_of_a_pickup_does_not_pivot(monkeypatch, db):
    session = _FakeSession({pickup.MARKER_KEY: 1, "pickup_type": "customer", "destination_address_id": str(GENERAL)})
    ai = _ai({"pickup_customer_name": "王小明"}, unmatched=UNMATCHED, content_type="continuation")
    _, reached = _pivot(monkeypatch, db, session=session, ai=ai, content="王小明")
    assert reached == []


def test_revision_to_pickup_naming_an_unmatched_address_does_not_pivot(monkeypatch, db):
    session = _FakeSession({pickup.MARKER_KEY: 1, "destination_address_id": str(PLAIN)})
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"}, unmatched=UNMATCHED, content_type="continuation")
    _, reached = _pivot(monkeypatch, db, session=session, ai=ai, content="这不是送货，是客户自己来提货，王小明")
    assert reached == []


def test_an_unresolved_question_does_not_pivot_either(monkeypatch, db):
    ai = _ai({"pickup_type": "customer"}, unmatched=UNMATCHED)
    _, reached = _pivot(monkeypatch, db, session=None, ai=ai, content="发给王小明")
    assert reached == []


def test_a_plain_delivery_to_an_unknown_address_still_pivots_as_before(monkeypatch, db):
    ai = _ai({}, unmatched=UNMATCHED)
    _, reached = _pivot(monkeypatch, db, session=None, ai=ai, content="发货到 Some New Co")
    assert reached == ["add_address"]


def test_documented_exception_a_delivery_revision_to_an_unknown_address_pivots(monkeypatch, db):
    """D12 exception (Smart Robot only): pickup -> delivery at an address NOT in
    the book takes the existing cancel-and-resubmit pivot."""
    session = _FakeSession({pickup.MARKER_KEY: 1, "pickup_type": "customer", "destination_address_id": str(GENERAL)})
    ai = _ai({"pickup_type": "none"}, unmatched=UNMATCHED, content_type="continuation")
    _, reached = _pivot(monkeypatch, db, session=session, ai=ai, content="不是自提，送到 Some New Co")
    assert reached == ["add_address"]


# ── the final check right before execution ────────────────────────────────────

def _confirm(monkeypatch, db, collected):
    session = _FakeSession(collected_fields=collected, status="pending_confirmation")
    executed = []
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    monkeypatch.setattr(workflow_engine, "_find_service_by_type_id", lambda context, type_id: _service())
    monkeypatch.setattr(workflow_engine, "_execute_workflow_and_finish", lambda *a, **k: executed.append(1))
    workflow_engine._handle_confirm({"serial_number": "REQ-1"}, db)
    return session, executed


def test_a_new_request_with_an_inconsistent_combination_is_not_executed(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "destination_address_id": str(GENERAL)}              # no pickup name
    session, executed = _confirm(monkeypatch, db, collected)
    assert executed == [] and session.status == "active"
    assert sent and "取货人" in sent[0]


def test_a_consistent_pickup_executes(monkeypatch, db, sent):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)}
    _, executed = _confirm(monkeypatch, db, collected)
    assert executed == [1] and sent == []


def test_a_grandfathered_request_pointing_at_a_retention_row_still_executes(monkeypatch, db, sent):
    collected = {"warehouse_code": "JFK", "destination_address_id": str(JFK_RET)}    # no marker: predates V46
    _, executed = _confirm(monkeypatch, db, collected)
    assert executed == [1] and sent == []
