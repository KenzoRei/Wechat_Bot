"""
Customer pickup on the Kefu channel (core/kefu_turn_apply.py wiring of
core/pickup.py) -- docs/ai-collaboration/2026-11-customer-pickup/plan.md.

The turn runs through the real apply_kefu_turn with an in-memory SQLite
database holding only the address table (so the resolver and sanitizer run for
real); the surrounding persistence helpers are the same fakes the other Kefu
state-machine tests use.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai.base import AddressMatch
from core import kefu_turn_apply, pickup
from models.uchoice import UchoiceAddress

GENERAL = uuid.UUID("a1000000-0042-0000-0000-000000000001")
JFK_RET = uuid.UUID("bb8c35cb-dda1-404d-9294-682ed3df5659")
PLAIN = uuid.UUID("c0ffee00-1111-4111-8111-111111111111")


def _row(address_id, company, charge, addr, wh, kind):
    return UchoiceAddress(address_id=address_id, company_name=company, charge_type=charge, addr=addr,
                          warehouse_code=wh, created_by="test", pickup_kind=kind,
                          created_at=datetime.now(timezone.utc))


class _HybridDB:
    """db.get() serves the fake request log; everything else is the real
    in-memory SQLite session (address table only); add/flush are recorded."""

    def __init__(self, sql, log):
        self._sql = sql
        self.log = log
        self.added = []

    def get(self, model, key):
        return self.log

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def __getattr__(self, name):
        return getattr(self._sql, name)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    UchoiceAddress.__table__.create(engine)
    sql = sessionmaker(bind=engine)()
    sql.add_all([
        _row(JFK_RET, "JFK仓库自提留存", "self_pickup", "14502 156th St", "JFK", "retention"),
        _row(GENERAL, "客户自提", "self_pickup", "", None, "customer"),
        _row(PLAIN, "ABC Trading", "delivery", "123 Main St", "JFK", None),
    ])
    sql.commit()
    log = SimpleNamespace(serial_number="REQ-1", customer_id=None, status="pending", origin_session_id=None, log_id="log-1")
    yield _HybridDB(sql, log)
    sql.close()


def _session(collected=None, status="active"):
    return SimpleNamespace(
        session_id="session-1", request_log_id="log-1", service_type_id="service-1", customer_id="customer-1",
        status=status, case_revision=1, collected_fields=dict(collected or {}), conversation_history=[],
        updated_at=None, expires_at=None,
    )


def _service():
    return {"name": "uchoice_outbound_request", "service_type_id": "00000000-0000-0000-0000-0000000000a1",
            "workflow_id": "workflow-1",
            "input_schema": {"required": []}, "requires_confirmation": True, "awaits_completion": True,
            "targets_existing_request": False}


def _ai(extracted=None, address_match=None):
    return SimpleNamespace(extracted_fields=extracted or {}, all_fields_collected=False, reply="ai reply",
                           address_match=address_match, unmatched_new_address=None)


def _ctx(content, candidates=None):
    return {"group_id": "00000000-0000-0000-0000-000000000001", "content": content, "warehouse_codes": None,
            "uchoice_candidates": {"addresses": candidates or []}}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(kefu_turn_apply.pre_confirm_validators, "run", lambda *args: None)
    monkeypatch.setattr(kefu_turn_apply, "_render_confirmation", lambda *args: "CONFIRM THIS")
    monkeypatch.setattr(kefu_turn_apply, "_outbound_stock_error", lambda *args: None)
    monkeypatch.setattr(kefu_turn_apply, "_resolve_outbound_pallet_defaults", lambda *args: None)

    def _no_pivot(*args, **kwargs):
        raise AssertionError("a pickup request must never pivot to an address case")
    monkeypatch.setattr(kefu_turn_apply, "_pivot_to_address", _no_pivot)


def _turn(db, session, ai, content, candidates=None):
    return kefu_turn_apply.apply_kefu_turn(db, _ctx(content, candidates), ai, _service(), session)


# ── the transition step ───────────────────────────────────────────────────────

def test_customer_pickup_resolves_the_row_and_goes_to_confirmation(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"})
    reply = _turn(db, session, ai, "王小明来自提")
    assert reply == "CONFIRM THIS" and session.status == "pending_confirmation"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)
    assert session.collected_fields["pickup_customer_name"] == "王小明"


def test_pickup_wins_over_an_ambiguous_address_match(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    match = AddressMatch(status="ambiguous", candidate_ids=(str(PLAIN), str(JFK_RET)))
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"}, address_match=match)
    reply = _turn(db, session, ai, "王小明来自提，他公司在 ABC", candidates=[{"address_id": str(PLAIN)}])
    assert reply == "CONFIRM THIS"                       # no "which one?" question
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


def test_pickup_wins_over_an_unmatched_address_and_never_pivots(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    match = AddressMatch(status="unmatched", new_address={"company_name": "New Co", "addr": "9 Elm St"})
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"}, address_match=match)
    reply = _turn(db, session, ai, "王小明来自提，他公司在 New Co")
    assert reply == "CONFIRM THIS"                       # _pivot_to_address is stubbed to fail the test if reached
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


def test_an_ai_written_pickup_row_id_is_stripped_before_it_can_be_the_destination(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    ai = _ai({"destination_address_id": str(GENERAL)})
    reply = _turn(db, session, ai, "发货")
    assert "destination_address_id" not in session.collected_fields
    assert reply == "CONFIRM THIS"       # (the required list is empty here, so the turn proceeds without a destination)


def test_a_disagreement_asks_the_question_and_changes_nothing(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"})
    reply = _turn(db, session, ai, "发给王小明 S2 3箱")
    assert reply == pickup.ASK_WHICH
    assert session.status == "active"
    assert "pickup_type" not in session.collected_fields and "destination_address_id" not in session.collected_fields
    assert session.conversation_history[-1] == {"role": "assistant", "content": pickup.ASK_WHICH}


def test_a_clarification_takes_a_pending_confirmation_back_to_collecting(db):
    """Audit finding 15: the unreviewed warehouse change that came with the vague
    pickup must not be confirmable -- a fresh summary is required."""
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "destination_address_id": str(PLAIN)},
                       status="pending_confirmation")
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明", "warehouse_code": "DE"})
    reply = _turn(db, session, ai, "发给王小明")
    assert reply == pickup.ASK_WHICH and session.status == "active"
    assert "pickup_type" not in session.collected_fields


def test_a_repeated_pickup_type_does_not_hide_a_negation_and_asks(db):
    """Audit finding 16."""
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                        "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)},
                       status="pending_confirmation")
    reply = _turn(db, session, _ai({"pickup_type": "customer"}), "不是自提，送到 ABC Trading")
    assert reply == pickup.ASK_WHICH and session.status == "active"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


def test_a_new_name_with_retention_asks_instead_of_being_dropped(db):
    """Audit finding 17."""
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"}, status="pending_confirmation")
    reply = _turn(db, session, _ai({"pickup_type": "retention", "pickup_customer_name": "张三"}), "JFK仓库自提留存，张三")
    assert reply == pickup.ASK_RETENTION_NAME and session.status == "active"
    assert "pickup_type" not in session.collected_fields


def test_bare_ziti_with_no_classification_asks_which_type(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    assert _turn(db, session, _ai({}), "S2 3箱 自提") == pickup.ASK_WHICH


def test_a_name_only_followup_keeps_the_pickup(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                        "destination_address_id": str(GENERAL)}, status="pending_confirmation")
    reply = _turn(db, session, _ai({"pickup_customer_name": "王小明"}), "王小明")
    assert reply == "CONFIRM THIS"
    assert session.collected_fields["pickup_type"] == "customer"
    assert session.collected_fields["pickup_customer_name"] == "王小明"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


def test_revision_from_pickup_to_a_delivery_address_in_the_book(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                        "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)},
                       status="pending_confirmation")
    match = AddressMatch(status="matched", candidate_ids=(str(PLAIN),))
    reply = _turn(db, session, _ai({"pickup_type": "none"}, address_match=match),
                  "不是自提，送到 ABC Trading", candidates=[{"address_id": str(PLAIN)}])
    assert reply == "CONFIRM THIS"
    assert session.collected_fields["destination_address_id"] == str(PLAIN)
    for gone in ("pickup_type", "pickup_customer_name"):
        assert gone not in session.collected_fields


def test_revision_to_pickup_replaces_a_matched_delivery_destination(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "destination_address_id": str(PLAIN)},
                       status="pending_confirmation")
    ai = _ai({"pickup_type": "customer", "pickup_customer_name": "王小明"})
    assert _turn(db, session, ai, "这不是送货，是客户自己来提货") == "CONFIRM THIS"
    assert session.collected_fields["destination_address_id"] == str(GENERAL)


def test_retention_resolves_to_the_source_warehouses_own_row(db):
    session = _session({pickup.MARKER_KEY: 1, "warehouse_code": "JFK"})
    ai = _ai({"pickup_type": "retention"})
    assert _turn(db, session, ai, "JFK仓库自提留存") == "CONFIRM THIS"
    assert session.collected_fields["destination_address_id"] == str(JFK_RET)
    assert "pickup_customer_name" not in session.collected_fields


# ── creation stamp ────────────────────────────────────────────────────────────

def test_a_new_outbound_case_is_stamped_at_creation(db):
    ai = _ai({"warehouse_code": "JFK"})
    _turn(db, None, ai, "发货")
    created = [obj for obj in db.added if obj.__class__.__name__ == "ConversationSession"]
    assert created and created[0].collected_fields.get(pickup.MARKER_KEY) == pickup.MARKER_VALUE


# ── the final check right before execution ────────────────────────────────────

def _confirm(monkeypatch, db, collected):
    session = _session(collected, status="pending_confirmation")
    finished = []
    monkeypatch.setattr(kefu_turn_apply, "_finish_execution", lambda *a, **k: finished.append(1) or "done")
    reply = kefu_turn_apply.confirm_kefu_turn(db, _ctx("确认"), _service(), session)
    return session, reply, finished


def test_confirm_rejects_an_inconsistent_new_request(monkeypatch, db):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "destination_address_id": str(GENERAL)}                      # no pickup name
    session, reply, finished = _confirm(monkeypatch, db, collected)
    assert finished == [] and session.status == "active" and "取货人" in reply


def test_confirm_executes_a_consistent_pickup(monkeypatch, db):
    collected = {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", "pickup_type": "customer",
                 "pickup_customer_name": "王小明", "destination_address_id": str(GENERAL)}
    _, _, finished = _confirm(monkeypatch, db, collected)
    assert finished == [1]


def test_confirm_still_executes_a_grandfathered_retention_request(monkeypatch, db):
    collected = {"warehouse_code": "JFK", "destination_address_id": str(JFK_RET)}       # predates V42: no marker
    _, _, finished = _confirm(monkeypatch, db, collected)
    assert finished == [1]
