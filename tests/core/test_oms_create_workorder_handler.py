"""
Direct handler-level coverage for handlers/oms_create_workorder.py's
customer-driven, failure-tolerant redesign (see
docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
§3.3). Real Postgres DB for the customer/customer_credential lookups;
clients.oms_client's actual HTTP calls are monkeypatched.

Deliberately handler-level, not a full conversational-turn test -- these
exercise the OMS decision logic directly (skip/succeed/fail branches)
without needing a whole label-creation session set up first.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from core import customer_directory as cd
import handlers.oms_create_workorder as oms_handler_module
from handlers.oms_create_workorder import OMSCreateWorkorderHandler


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _base_context(customer_id: str, oms_order_no: str = "") -> dict:
    return {
        "collected_fields": {"billing_customer_id": customer_id, "oms_outbound_order_no": oms_order_no},
        "result": {"tracking_number": "TRACK123"},
        "serial_number": "REQ-20260910-000001",
    }


def test_skips_cleanly_when_no_oms_credentials_configured():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Creds Co")
        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id), {}, db=db)
        assert result == {"oms_work_order": None, "oms_error": None}
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_records_config_error_when_oms_wh_code_missing_but_credentials_present():
    """The exact gap Codex flagged: valid OMS credentials but no wh_code
    configured must be a recorded, non-fatal error -- not the old hard
    RuntimeError."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No WhCode Co")  # oms_wh_code left unset
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id), {}, db=db)
        assert result["oms_work_order"] is None
        assert result["oms_error"] is not None
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_case_a_creates_unlinked_work_order_when_no_order_number_given(monkeypatch):
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Case A Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        monkeypatch.setattr(oms_handler_module, "create_work_order", lambda **kw: "WO-UNLINKED-001")
        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id), {}, db=db)
        assert result == {"oms_work_order": "WO-UNLINKED-001"}
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_case_b_links_when_order_found(monkeypatch):
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Case B Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        monkeypatch.setattr(oms_handler_module, "query_outbound_order", lambda *a, **kw: {"whCode": "DE19713"})
        captured = {}
        def fake_create(**kw):
            captured.update(kw)
            return "WO-LINKED-001"
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id, oms_order_no="OBS123"), {}, db=db)
        assert result == {"oms_work_order": "WO-LINKED-001", "oms_order_linked": "OBS123"}
        assert captured["associated_tracking_no"] == "OBS123"
        assert captured["associated_tracking_no_type"] == 2
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_case_b_lookup_miss_falls_back_to_unlinked_with_remark_note_not_false_link(monkeypatch):
    """§3.1's fix: a lookup failure must NOT create a work order that falsely
    claims a verified link -- it falls back to Case A's shape, preserving
    the raw customer-supplied order number for create_work_order to note in
    the remark."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Lookup Miss Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        def fake_query(*a, **kw):
            raise RuntimeError("OMS outbound order not found: OBS999")
        monkeypatch.setattr(oms_handler_module, "query_outbound_order", fake_query)

        captured = {}
        def fake_create(**kw):
            captured.update(kw)
            return "WO-FALLBACK-001"
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id, oms_order_no="OBS999"), {}, db=db)
        assert result == {"oms_work_order": "WO-FALLBACK-001"}
        assert "associated_tracking_no" not in captured  # no false link claimed
        assert captured["unmatched_oms_order_no"] == "OBS999"  # preserved for the remark
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_query_outbound_order_timeout_falls_back_to_unlinked_not_raised(monkeypatch):
    """The exact gap Codex reproduced: query_outbound_order raising anything
    other than RuntimeError (a real transport failure, not just a business
    'not found') must not escape uncaught -- it has to degrade the same way
    as a lookup miss."""
    import requests

    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Timeout Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        def fake_query(*a, **kw):
            raise requests.exceptions.Timeout("connection timed out")
        monkeypatch.setattr(oms_handler_module, "query_outbound_order", fake_query)

        captured = {}
        def fake_create(**kw):
            captured.update(kw)
            return "WO-AFTER-TIMEOUT-001"
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id, oms_order_no="OBS777"), {}, db=db)
        assert result == {"oms_work_order": "WO-AFTER-TIMEOUT-001"}
        assert "associated_tracking_no" not in captured
        assert captured["unmatched_oms_order_no"] == "OBS777"
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_query_outbound_order_timeout_then_create_also_fails_records_error_not_raised(monkeypatch):
    """If OMS is genuinely down, the fallback create_work_order attempt
    will likely also fail -- must still end in a recorded oms_error, not a
    second uncaught exception."""
    import requests

    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Fully Down Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        monkeypatch.setattr(oms_handler_module, "query_outbound_order", lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError("no route")))
        monkeypatch.setattr(oms_handler_module, "create_work_order", lambda **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError("no route")))

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id, oms_order_no="OBS888"), {}, db=db)
        assert result["oms_work_order"] is None
        assert result["oms_error"] is not None
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_create_work_order_failure_is_recorded_not_raised(monkeypatch):
    """The atomicity-critical case: create_work_order itself failing must
    never propagate -- it has to be caught and recorded so the already-
    created label still gets delivered."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="API Fail Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        def fake_create(**kw):
            raise RuntimeError("OMS API error [500]: internal error")
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        result = OMSCreateWorkorderHandler().handle(_base_context(customer_id), {}, db=db)
        assert result["oms_work_order"] is None
        assert "internal error" in result["oms_error"]
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_logistics_fee_qty_derived_from_sales_amount_and_truncated(monkeypatch):
    """logistics_fee_qty must be int(sales_amount) -- the estimated quote,
    truncated per the explicit "qty = int(quote price)" requirement --
    and passed through to create_work_order."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="VAS Qty Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        captured = {}
        def fake_create(**kw):
            captured.update(kw)
            return "WO-VAS-001"
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        context = _base_context(customer_id)
        context["result"]["sales_amount"] = 42.99
        OMSCreateWorkorderHandler().handle(context, {}, db=db)
        assert captured["logistics_fee_qty"] == 42
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_logistics_fee_qty_none_when_sales_amount_missing(monkeypatch):
    """No quote (e.g. it failed non-fatally) -- must not guess a qty, just
    omit the VAS line entirely (create_work_order treats None as no-op)."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Quote Co", oms_wh_code="DE19713")
        cd.set_credential(db, customer_id, "oms_app_key", "key", actor="test")
        cd.set_credential(db, customer_id, "oms_app_secret", "secret", actor="test")

        captured = {}
        def fake_create(**kw):
            captured.update(kw)
            return "WO-NOVAS-001"
        monkeypatch.setattr(oms_handler_module, "create_work_order", fake_create)

        OMSCreateWorkorderHandler().handle(_base_context(customer_id), {}, db=db)
        assert captured["logistics_fee_qty"] is None
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_records_label_shipment_row_regardless_of_oms_outcome(monkeypatch):
    """Companion ledger (plan §2): one row per label, linked to request_log,
    populated even when OMS is skipped entirely (no credentials)."""
    from models.request_log import RequestLog
    from models.customer import LabelShipment

    db = SessionLocal()
    customer_id = _fresh_customer_id()
    log_id = None
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Ledger Co")
        log = RequestLog(raw_message="test", status="processing", source_channel="smart_robot")
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.log_id

        context = _base_context(customer_id)
        context["request_log_id"] = str(log_id)
        # sales_amount here mirrors what handlers/label/base.py's own
        # get_price_quote wiring would have already put in context["result"]
        # by the time this step runs (context["result"].update(...) across
        # steps) -- not re-fetched by this handler.
        context["result"] = {"tracking_number": "TRACK123", "carrier": "fedex", "sales_amount": 7.83}

        result = OMSCreateWorkorderHandler().handle(context, {}, db=db)
        assert result == {"oms_work_order": None, "oms_error": None}

        shipment = db.query(LabelShipment).filter_by(request_log_id=log_id).one()
        assert shipment.billing_customer_id == customer_id
        assert shipment.carrier == "fedex"
        assert shipment.tracking_number == "TRACK123"
        assert shipment.oms_work_order is None
        assert shipment.oms_error is None
        assert float(shipment.sales_amount) == 7.83
        assert shipment.status == "created"
    finally:
        db.execute(text("delete from label_shipment where request_log_id = :lid"), {"lid": log_id})
        if log_id:
            db.execute(text("delete from request_log where log_id = :lid"), {"lid": log_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_missing_billing_customer_id_raises_precondition_error():
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="billing_customer_id"):
            OMSCreateWorkorderHandler().handle({
                "collected_fields": {}, "result": {"tracking_number": "T1"}, "serial_number": "REQ-1",
            }, {}, db=db)
    finally:
        db.close()


def test_missing_tracking_number_raises_precondition_error():
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="tracking_number"):
            OMSCreateWorkorderHandler().handle({
                "collected_fields": {"billing_customer_id": "F000001"}, "result": {}, "serial_number": "REQ-1",
            }, {}, db=db)
    finally:
        db.close()
