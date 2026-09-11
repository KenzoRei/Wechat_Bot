"""
Direct handler-level coverage for handlers/label/base.py's credential
cutover (customer_credential/customer, not group_service.config -- see
docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
§3.5). clients.yidida_client's actual HTTP call is monkeypatched.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from core import customer_directory as cd
import handlers.label.base as label_base_module
from handlers.label.fedex import FedExLabelHandler


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _context(customer_id: str) -> dict:
    return {
        "collected_fields": {
            "billing_customer_id": customer_id,
            "shipper_name": "Warehouse", "shipper_phone": "111", "shipper_street": "1 Main St",
            "shipper_city": "City", "shipper_state": "ST", "shipper_zip": "00000",
            "recipient_name": "Recipient", "recipient_phone": "222", "recipient_street": "2 Elm St",
            "recipient_city": "City2", "recipient_state": "ST2", "recipient_zip": "11111",
            "weight_lbs": 5,
        },
        "group_description": "TestGroup",
        "display_name": "Simon",
        "serial_number": "REQ-20260910-000001",
    }


def test_uses_customer_credential_not_group_config(monkeypatch):
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Label Cred Co", ydd_channel_id={"fedex": "channel_x"})
        cd.set_credential(db, customer_id, "ydd_username", "real_username", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "real_password", actor="test")

        captured = {}
        def fake_create_label(carrier, fields, api_key):
            captured["carrier"] = carrier
            captured["fields"] = fields
            captured["api_key"] = api_key
            return {"tracking_number": "TRACK1", "label_base64": "BASE64"}
        monkeypatch.setattr(label_base_module, "create_label", fake_create_label)

        # group config is deliberately empty/garbage -- must never be read.
        result = FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex", "ydd_api_key": "WRONG", "ydd_cust_id": "WRONG"}, db=db)

        assert result == {"tracking_number": "TRACK1", "label_base64": "BASE64", "carrier": "fedex", "sales_amount": None}
        assert captured["api_key"] == "real_password"
        assert captured["fields"]["ydd_cust_id"] == "real_username"
        assert captured["fields"]["ydd_channel_id"] == "channel_x"
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_carrier_rejection_becomes_label_creation_rejected(monkeypatch):
    """
    ShipmentRejected (a business rejection -- bad phone, bad address, etc.,
    clients.yidida_client's own distinct exception for this case) must be
    translated into core.workflow_errors.LabelCreationRejected, carrying a
    real, actionable reply -- not left as a raw exception that propagates
    silently (the bug this fix addresses: see the 2026-09-11 session
    discussion). A genuine infra failure (plain RuntimeError) must NOT be
    caught here -- only ShipmentRejected specifically.
    """
    from clients.yidida_client import ShipmentRejected
    from core.workflow_errors import LabelCreationRejected

    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Rejection Co", ydd_channel_id={"fedex": "channel_z"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        carrier_message = '{"response":{"errors":[{"code":"120313","message":"ShipFrom PhoneNumber must be at least 10 alphanumeric characters"}]}}'

        def fake_create_label(carrier, fields, api_key):
            raise ShipmentRejected(carrier_message)
        monkeypatch.setattr(label_base_module, "create_label", fake_create_label)

        with pytest.raises(LabelCreationRejected) as exc_info:
            FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)

        assert exc_info.value.carrier_message == carrier_message
        assert "标签创建失败" in exc_info.value.user_message
        assert carrier_message in exc_info.value.user_message
        assert "请核实相关信息后重新提供并确认" in exc_info.value.user_message
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_ydd_username_can_differ_from_customer_id(monkeypatch):
    """Business rule from the plan: ydd_username is USUALLY but not always
    equal to customer_id -- must never be derived, always read from storage."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Exception Co", ydd_channel_id={"fedex": "channel_y"})
        cd.set_credential(db, customer_id, "ydd_username", "totally-different-login", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "pw", actor="test")

        captured = {}
        monkeypatch.setattr(label_base_module, "create_label", lambda carrier, fields, api_key: captured.update(fields=fields) or {"tracking_number": "T", "label_base64": "B"})

        FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
        assert captured["fields"]["ydd_cust_id"] == "totally-different-login"
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_missing_credentials_raises():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Creds Co")
        with pytest.raises(RuntimeError, match="ydd_username/ydd_password"):
            FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_missing_channel_id_for_carrier_raises():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Channel Co", ydd_channel_id={"ups": "channel_only_for_ups"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")
        with pytest.raises(RuntimeError, match="ydd_channel_id"):
            FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_records_sales_amount_from_price_quote(monkeypatch):
    """/price is a genuinely separate endpoint from /yundans (verified: a
    real /yundans response carries no pricing field at all) -- confirms
    the quote's moneyTotal ends up as sales_amount in the handler result."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Quote Co", ydd_channel_id={"fedex": "UPS Ground NJ"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        monkeypatch.setattr(label_base_module, "create_label", lambda carrier, fields, api_key: {"tracking_number": "T1", "label_base64": "B1"})

        captured = {}
        def fake_get_price_quote(fields, api_key):
            captured["fields"] = fields
            captured["api_key"] = api_key
            return {"sales_amount": 7.83}
        monkeypatch.setattr(label_base_module, "get_price_quote", fake_get_price_quote)

        result = FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
        assert result["sales_amount"] == 7.83
        assert captured["api_key"] == "p"
        assert captured["fields"]["ydd_channel_id"] == "UPS Ground NJ"
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_price_quote_failure_does_not_block_label_delivery(monkeypatch):
    """The label has already been created (and paid for) by the time the
    quote is attempted -- a quote failure must never cost the customer
    their label, same failure-tolerance principle as OMS."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Quote Fail Co", ydd_channel_id={"fedex": "ch"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        monkeypatch.setattr(label_base_module, "create_label", lambda carrier, fields, api_key: {"tracking_number": "T1", "label_base64": "B1"})
        def fake_get_price_quote(fields, api_key):
            raise RuntimeError("YiDiDa price query failed: simulated")
        monkeypatch.setattr(label_base_module, "get_price_quote", fake_get_price_quote)

        result = FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
        assert result["tracking_number"] == "T1"
        assert result["sales_amount"] is None
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_revalidates_customer_status_at_execution_time_not_just_collection_time(monkeypatch):
    """The gap Codex flagged: a customer deactivated between field
    collection and confirmation must be caught here, at the last point
    before an irreversible YDD call -- not just at collection time."""
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Deactivate Mid-Flow Co", status="active", ydd_channel_id={"fedex": "ch"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        # Deactivate AFTER the field was collected (billing_customer_id is
        # already sitting in collected_fields from an earlier turn).
        cd.upsert_customer(db, customer_id, actor="test", status="inactive")

        monkeypatch.setattr(label_base_module, "create_label", lambda **kw: pytest.fail("YDD must never be called for a deactivated customer"))

        with pytest.raises(RuntimeError, match="revalidation failed"):
            FedExLabelHandler().handle(_context(customer_id), {"carrier": "fedex"}, db=db)
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_revalidation_rejects_when_customer_role_requester_rebound_since_collection(monkeypatch):
    """A customer-role requester whose own binding changed between
    collection and confirmation must not be able to complete a label
    against the OLD (now-stale) billing_customer_id."""
    db = SessionLocal()
    old_id, new_id = _fresh_customer_id(), _fresh_customer_id()
    try:
        cd.upsert_customer(db, old_id, actor="test", display_name="Old Binding Co", status="active", ydd_channel_id={"fedex": "ch"})
        cd.upsert_customer(db, new_id, actor="test", display_name="New Binding Co", status="active")
        cd.set_credential(db, old_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, old_id, "ydd_password", "p", actor="test")

        ctx = _context(old_id)
        ctx["role"] = "customer"
        ctx["requester_billing_customer_id"] = new_id  # rebound since the field was collected

        monkeypatch.setattr(label_base_module, "create_label", lambda **kw: pytest.fail("YDD must never be called against a stale binding"))

        with pytest.raises(RuntimeError, match="revalidation failed"):
            FedExLabelHandler().handle(ctx, {"carrier": "fedex"}, db=db)
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": old_id})
        db.execute(text("delete from customer where customer_id in (:a, :b)"), {"a": old_id, "b": new_id})
        db.commit()
        db.close()


def test_missing_billing_customer_id_raises():
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="billing_customer_id"):
            FedExLabelHandler().handle({"collected_fields": {}}, {"carrier": "fedex"}, db=db)
    finally:
        db.close()
