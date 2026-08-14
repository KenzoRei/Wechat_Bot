"""
Smart Robot / Kefu parity plan (docs/ai-collaboration/smart-robot-kefu-parity-plan.md),
signed by both agents, user-authorized implementation.

core/workflow_engine.py's readiness branch points (_handle_new_request,
_handle_continuation) used to trust ai_response.all_fields_collected -- the
AI's OWN claim, computed BEFORE _sanitize_extracted_fields_before_persistence
runs. If sanitization silently drops the field the claim was based on, a
stale True could bypass a missing-field re-prompt -- the same bug fixed on
the Kefu side in commit 38c812a.

Unlike Kefu, Smart Robot had no generic input_schema.required readiness
predicate at all -- deleting only the AI flag would have stranded FedEx/UPS
and most other services, since neither of the other two disjuncts
(auto_resolved, _outbound_required_fields_present) apply to them. This
module tests the new _all_required_fields_present predicate and its wiring
into both branch points using fake session and database objects, with no real
PostgreSQL or external API calls.
"""
from types import SimpleNamespace

import pytest

from core import workflow_engine

_GROUP_ID = "00000000-0000-0000-0000-000000000001"
_SERVICE_ID = "00000000-0000-0000-0000-000000000002"
_WORKFLOW_ID = "00000000-0000-0000-0000-000000000003"

class _FakeSession:
    def __init__(self, service_type_id=_SERVICE_ID, collected_fields=None, request_log_id=None, status="active"):
        self.session_id = "sess-1"
        self.service_type_id = service_type_id
        self.collected_fields = collected_fields or {}
        self.request_log_id = request_log_id
        self.status = status
        self.conversation_history = []


class _FakeDB:
    """Every session_manager/request_logger call touching this is monkeypatched below."""
    def query(self, *args, **kwargs):
        raise AssertionError("unexpected direct db.query in a readiness-branch test")

    def commit(self):
        pass

    def rollback(self):
        pass

    def add(self, *args, **kwargs):
        pass

    def refresh(self, *args, **kwargs):
        pass


def _service(name, required, *, targets_existing_request=False, service_type_id=_SERVICE_ID):
    return {
        "name": name,
        "service_type_id": service_type_id,
        "workflow_id": _WORKFLOW_ID,
        "input_schema": {"required": required},
        "targets_existing_request": targets_existing_request,
        "requires_confirmation": True,
    }


def _ai_response(all_fields_collected, extracted_fields=None, reply="ai reply", service_type_name=None):
    return SimpleNamespace(
        all_fields_collected=all_fields_collected,
        extracted_fields=extracted_fields or {},
        reply=reply,
        service_type_name=service_type_name,
        intent="new_request",
        unmatched_new_address=None,
    )


@pytest.fixture
def progressed(monkeypatch):
    """Records whether _on_all_fields_collected was reached, without running
    the real confirmation/execution pipeline (out of scope for a readiness-
    branch test)."""
    calls = []
    monkeypatch.setattr(workflow_engine, "_on_all_fields_collected", lambda *a, **k: calls.append(1))
    return calls


@pytest.fixture(autouse=True)
def _stub_session_writes(monkeypatch):
    """update_collected_fields/add_message/close_session write to the fake
    session directly instead of hitting a real DB session."""
    def _update(db, session, fields):
        session.collected_fields = {**session.collected_fields, **fields}
    monkeypatch.setattr(workflow_engine.session_manager, "update_collected_fields", _update)
    monkeypatch.setattr(workflow_engine.session_manager, "add_message", lambda *a, **k: None)

    def _close(db, session, status, commit=True):
        session.status = status
    monkeypatch.setattr(workflow_engine.session_manager, "close_session", _close)
    monkeypatch.setattr(workflow_engine, "send_message", lambda *a, **k: None)


# ── 1/2: stale-claim regression (new-request + continuation) ──────────────

def test_new_request_stale_all_fields_collected_claim_cannot_bypass_missing_field(monkeypatch, progressed):
    """
    Live incident class this guards against: the AI claims
    all_fields_collected=True while extracting a malformed sku_lines value
    that _sanitize_extracted_fields_before_persistence correctly drops. The
    turn must stay in field-collection, never reach _on_all_fields_collected
    on the strength of the AI's now-stale claim.
    """
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: {},  # sanitizer drops the malformed field entirely
    )
    session = _FakeSession(collected_fields={})
    monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.request_logger, "create_log", lambda *a, **k: SimpleNamespace(log_id="log-1", serial_number="REQ-1"))

    service = _service("uchoice_inbound_request", required=["sku_lines"])
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "库存", "msg_id": "m1", "allowed_services": [service]}
    ai = _ai_response(all_fields_collected=True, extracted_fields={"sku_lines": "库存"},
                      service_type_name="uchoice_inbound_request")

    workflow_engine._handle_new_request(context, ai, _FakeDB())

    assert progressed == []
    assert "sku_lines" not in session.collected_fields


def test_continuation_stale_all_fields_collected_claim_cannot_bypass_missing_field(monkeypatch, progressed):
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: {},
    )
    service = _service("uchoice_inbound_request", required=["sku_lines"])
    session = _FakeSession(service_type_id=service["service_type_id"], collected_fields={})
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)

    context = {"session_id": "sess-1", "content": "库存", "allowed_services": [service], "group_id": "g1"}
    ai = _ai_response(all_fields_collected=True, extracted_fields={"sku_lines": "库存"})

    workflow_engine._handle_continuation(context, ai, _FakeDB())

    assert progressed == []
    assert "sku_lines" not in session.collected_fields


# ── 3/4: FedEx/UPS complete/incomplete matrix ──────────────────────────────

_CARRIER_REQUIRED = [
    "shipper_name", "shipper_phone", "shipper_street", "shipper_city", "shipper_state", "shipper_zip",
    "recipient_name", "recipient_phone", "recipient_street", "recipient_city", "recipient_state", "recipient_zip",
    "weight_lbs",
]


def _all_carrier_fields():
    return {field: "x" for field in _CARRIER_REQUIRED}


@pytest.mark.parametrize("carrier", ["fedex_label", "ups_label"])
def test_carrier_new_request_progresses_with_all_13_fields_and_stale_false_flag(monkeypatch, progressed, carrier):
    """The AI flag is False (would have blocked progression pre-fix) but
    every one of the carrier's 13 required fields is genuinely present --
    must still progress on the schema predicate alone."""
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    session = _FakeSession(collected_fields={})
    monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.request_logger, "create_log", lambda *a, **k: SimpleNamespace(log_id="log-1", serial_number="REQ-1"))

    service = _service(carrier, required=_CARRIER_REQUIRED)
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "ship it", "msg_id": "m1", "allowed_services": [service]}
    ai = _ai_response(all_fields_collected=False, extracted_fields=_all_carrier_fields(),
                      service_type_name=carrier)

    workflow_engine._handle_new_request(context, ai, _FakeDB())

    assert progressed == [1]


@pytest.mark.parametrize("carrier", ["fedex_label", "ups_label"])
def test_carrier_new_request_does_not_progress_with_one_missing_field_and_stale_true_flag(monkeypatch, progressed, carrier):
    """The AI flag is True (would have wrongly forced progression pre-fix)
    but one of the 13 required fields is genuinely absent -- must stay in
    field collection."""
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    session = _FakeSession(collected_fields={})
    monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.request_logger, "create_log", lambda *a, **k: SimpleNamespace(log_id="log-1", serial_number="REQ-1"))

    incomplete = _all_carrier_fields()
    del incomplete["weight_lbs"]
    service = _service(carrier, required=_CARRIER_REQUIRED)
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "ship it", "msg_id": "m1", "allowed_services": [service]}
    ai = _ai_response(all_fields_collected=True, extracted_fields=incomplete,
                      service_type_name=carrier)

    workflow_engine._handle_new_request(context, ai, _FakeDB())

    assert progressed == []
    assert "weight_lbs" not in session.collected_fields


@pytest.mark.parametrize("carrier", ["fedex_label", "ups_label"])
def test_carrier_continuation_complete_incomplete_matrix(monkeypatch, progressed, carrier):
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    service = _service(carrier, required=_CARRIER_REQUIRED)

    # Incomplete: 12 of 13 fields already present, flag stale-True.
    session = _FakeSession(service_type_id=service["service_type_id"], collected_fields={
        f: "x" for f in _CARRIER_REQUIRED if f != "weight_lbs"
    })
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    context = {"session_id": "sess-1", "content": "still going", "allowed_services": [service], "group_id": "g1"}
    ai = _ai_response(all_fields_collected=True, extracted_fields={})
    workflow_engine._handle_continuation(context, ai, _FakeDB())
    assert progressed == []

    # Now the last field arrives, flag stale-False -- must progress.
    ai2 = _ai_response(all_fields_collected=False, extracted_fields={"weight_lbs": "10"})
    workflow_engine._handle_continuation(context, ai2, _FakeDB())
    assert progressed == [1]


# ── 5: empty-required service ──────────────────────────────────────────────

def test_empty_required_service_progresses_immediately_with_stale_false_flag(monkeypatch, progressed):
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    session = _FakeSession(collected_fields={})
    monkeypatch.setattr(workflow_engine.session_manager, "create_session", lambda *a, **k: session)
    monkeypatch.setattr(workflow_engine.request_logger, "create_log", lambda *a, **k: SimpleNamespace(log_id="log-1", serial_number="REQ-1"))

    service = _service("view_storage", required=[])
    context = {"wechat_openid": "o1", "group_id": _GROUP_ID, "content": "库存", "msg_id": "m1", "allowed_services": [service]}
    ai = _ai_response(all_fields_collected=False, extracted_fields={},
                      service_type_name="view_storage")

    workflow_engine._handle_new_request(context, ai, _FakeDB())

    assert progressed == [1]


# ── 6: representative non-outbound U-Choice service ────────────────────────

def test_adjust_storage_readiness_independent_of_ai_flag(monkeypatch, progressed):
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    service = _service("adjust_storage", required=["warehouse_code", "adjustment_lines"])

    # Complete fields, flag stale-False -- must progress.
    session = _FakeSession(service_type_id=service["service_type_id"], collected_fields={
        "warehouse_code": "JFK", "adjustment_lines": [{"sku_code": "s1"}],
    })
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    context = {"session_id": "sess-1", "content": "ok", "allowed_services": [service], "group_id": "g1"}
    ai = _ai_response(all_fields_collected=False, extracted_fields={})
    workflow_engine._handle_continuation(context, ai, _FakeDB())
    assert progressed == [1]

    # Incomplete fields, flag stale-True -- must not progress.
    progressed.clear()
    session2 = _FakeSession(service_type_id=service["service_type_id"], collected_fields={"warehouse_code": "JFK"})
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session2)
    ai2 = _ai_response(all_fields_collected=True, extracted_fields={})
    workflow_engine._handle_continuation(context, ai2, _FakeDB())
    assert progressed == []


# ── 7: uchoice_outbound_request keeps its special override ────────────────

def test_outbound_request_progresses_via_its_own_override_with_stale_false_flag(monkeypatch, progressed):
    """_outbound_required_fields_present must still independently trigger
    progression -- it (and the generic schema predicate, which for outbound
    checks the same two top-level fields) must not have been removed."""
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    service = _service("uchoice_outbound_request", required=["sku_lines", "destination_address_id"])
    session = _FakeSession(service_type_id=service["service_type_id"], collected_fields={
        "sku_lines": [{"sku_code": "s1", "box_count": 10}],
        "destination_address_id": "addr-1",
    })
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    context = {"session_id": "sess-1", "content": "ok", "allowed_services": [service], "group_id": "g1"}
    ai = _ai_response(all_fields_collected=False, extracted_fields={})

    workflow_engine._handle_continuation(context, ai, _FakeDB())

    assert progressed == [1]


# ── 8: single eligible target candidate still auto-resolves ───────────────

def test_single_pending_candidate_auto_resolves_reference_serial_and_progresses(monkeypatch, progressed):
    monkeypatch.setattr(
        workflow_engine, "_sanitize_extracted_fields_before_persistence",
        lambda service_name, extracted, db, group_id: extracted,
    )
    service = _service("confirm_inbound_completion", required=["reference_serial"], targets_existing_request=True)
    session = _FakeSession(service_type_id=service["service_type_id"], collected_fields={})
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)

    context = {
        "session_id": "sess-1", "content": "确认收货", "allowed_services": [service], "group_id": "g1",
        "uchoice_candidates": {"pending_inbound_requests": [{"serial_number": "REQ-1"}]},
    }
    ai = _ai_response(all_fields_collected=False, extracted_fields={})

    workflow_engine._handle_continuation(context, ai, _FakeDB())

    assert progressed == [1]
    assert session.collected_fields["reference_serial"] == "REQ-1"


# ── 9: cancellation naming ──────────────────────────────────────────────────

def test_cancel_owned_log_names_serial_and_marks_cancelled(monkeypatch):
    service = _service("uchoice_inbound_request", required=["sku_lines"])
    session = _FakeSession(service_type_id=service["service_type_id"], request_log_id="log-1")
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda db, s, status, commit=True: setattr(s, "status", status))

    marked = []
    monkeypatch.setattr(workflow_engine.request_logger, "mark_cancelled", lambda db, log_id: marked.append(log_id))

    log = SimpleNamespace(serial_number="REQ-42")

    class _DBWithLog:
        def query(self, model):
            class _Q:
                def filter_by(self, **kwargs):
                    return self
                def first(self):
                    return log
            return _Q()

    replies = []
    monkeypatch.setattr(workflow_engine, "send_message", lambda context, content: replies.append(content))

    context = {"session_id": "sess-1", "content": "取消", "allowed_services": [service], "group_id": "g1"}
    workflow_engine._handle_cancel(context, _DBWithLog())

    assert marked == ["log-1"]
    assert session.status == "cancelled"
    assert replies == ["已取消（REQ-42），您可以随时发起新申请。"]


def test_cancel_referenced_log_omits_serial_and_leaves_it_unchanged(monkeypatch):
    """A targets_existing_request session's log is the ORIGINAL request it
    references, not one it owns -- must not be marked cancelled, and the
    message must not name it (would falsely imply the original request
    itself was cancelled)."""
    service = _service("confirm_inbound_completion", required=["reference_serial"], targets_existing_request=True)
    session = _FakeSession(service_type_id=service["service_type_id"], request_log_id="log-original")
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: session)
    monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda db, s, status, commit=True: setattr(s, "status", status))

    marked = []
    monkeypatch.setattr(workflow_engine.request_logger, "mark_cancelled", lambda db, log_id: marked.append(log_id))

    replies = []
    monkeypatch.setattr(workflow_engine, "send_message", lambda context, content: replies.append(content))

    context = {"session_id": "sess-1", "content": "取消", "allowed_services": [service], "group_id": "g1"}
    workflow_engine._handle_cancel(context, _FakeDB())

    assert marked == []
    assert session.status == "cancelled"
    assert replies == ["已取消，您可以随时发起新申请。"]


def test_cancel_with_no_session_uses_bare_message(monkeypatch):
    monkeypatch.setattr(workflow_engine, "_get_session", lambda context, db: None)
    replies = []
    monkeypatch.setattr(workflow_engine, "send_message", lambda context, content: replies.append(content))

    context = {"session_id": None, "content": "取消", "allowed_services": [], "group_id": "g1"}
    workflow_engine._handle_cancel(context, _FakeDB())

    assert replies == ["已取消，您可以随时发起新申请。"]
