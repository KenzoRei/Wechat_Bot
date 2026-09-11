"""
core/confirmation.py's fedex_label/ups_label pre-confirm quote section --
shows an ESTIMATED price before the customer commits (previously
sales_amount was only ever fetched, and only ever stored, AFTER the label
was already created -- see the 2026-09-11 label-pipeline discussion).
Non-fatal like every other quote/OMS call in this pipeline.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from core import customer_directory as cd
from core import confirmation


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _fields(customer_id: str, **overrides) -> dict:
    base = {
        "billing_customer_id": customer_id,
        "shipper_name": "Warehouse", "shipper_phone": "1234567890", "shipper_street": "1 Main St",
        "shipper_city": "City", "shipper_state": "ST", "shipper_zip": "00000",
        "recipient_name": "Recipient", "recipient_phone": "1234567890", "recipient_street": "2 Elm St",
        "recipient_city": "City2", "recipient_state": "NJ", "recipient_zip": "11111",
        "weight_lbs": 5,
    }
    base.update(overrides)
    return base


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _cleanup(db, customer_id):
    db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
    db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
    db.commit()


def test_quote_section_appended_on_success(db, monkeypatch):
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Quote Confirm Co", ydd_channel_id={"fedex": "ch1"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        monkeypatch.setattr(
            "clients.yidida_client.get_price_quote",
            lambda fields, api_key: {"sales_amount": 42.5},
        )

        sections = confirmation.build_confirmation_sections("fedex_label", _fields(customer_id), db)
        quote_items = [s for s in sections if s.get("type") == "raw" and "预计费用" in s["items"][0]]
        assert len(quote_items) == 1
        assert "$42.50" in quote_items[0]["items"][0]
        assert "实际费用以标签生成后为准" in quote_items[0]["items"][0]
    finally:
        _cleanup(db, customer_id)


def test_quote_section_omitted_on_quote_failure(db, monkeypatch):
    """Non-fatal: a quote failure must never block or corrupt the
    confirmation -- it just has no price section."""
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Quote Fail Confirm Co", ydd_channel_id={"fedex": "ch1"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        def boom(fields, api_key):
            raise RuntimeError("simulated quote failure")
        monkeypatch.setattr("clients.yidida_client.get_price_quote", boom)

        sections = confirmation.build_confirmation_sections("fedex_label", _fields(customer_id), db)
        assert not any(s.get("type") == "raw" and "预计费用" in s["items"][0] for s in sections)
        # the rest of the confirmation must still render normally
        assert any(s["label"] == "发件人" for s in sections)
        assert any(s["label"] == "收件人" for s in sections)
    finally:
        _cleanup(db, customer_id)


def test_quote_section_omitted_without_credentials(db):
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Creds Confirm Co")
        sections = confirmation.build_confirmation_sections("fedex_label", _fields(customer_id), db)
        assert not any(s.get("type") == "raw" and "预计费用" in s["items"][0] for s in sections)
    finally:
        _cleanup(db, customer_id)


def test_quote_section_omitted_without_channel(db):
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="No Channel Confirm Co", ydd_channel_id={"ups": "ch_ups_only"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        sections = confirmation.build_confirmation_sections("fedex_label", _fields(customer_id), db)
        assert not any(s.get("type") == "raw" and "预计费用" in s["items"][0] for s in sections)
    finally:
        _cleanup(db, customer_id)


def test_quote_section_omitted_without_billing_customer_id(db):
    """billing_customer_id should always be present by the time a full
    confirmation is built (it's a required input field), but the quote
    lookup must fail closed, not raise, if it's ever missing."""
    fields = _fields("placeholder")
    del fields["billing_customer_id"]
    sections = confirmation.build_confirmation_sections("fedex_label", fields, db)
    assert not any(s.get("type") == "raw" and "预计费用" in s["items"][0] for s in sections)


def test_ups_and_fedex_use_their_own_channel(db, monkeypatch):
    """Regression guard for the factory closure itself: fedex_label must
    look up ydd_channel_id['fedex'], ups_label must look up ['ups'] --
    not always the same hardcoded carrier."""
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Dual Channel Co", ydd_channel_id={"fedex": "fedex_ch", "ups": "ups_ch"})
        cd.set_credential(db, customer_id, "ydd_username", "u", actor="test")
        cd.set_credential(db, customer_id, "ydd_password", "p", actor="test")

        seen_channels = []
        def fake_quote(fields, api_key):
            seen_channels.append(fields["ydd_channel_id"])
            return {"sales_amount": 10.0}
        monkeypatch.setattr("clients.yidida_client.get_price_quote", fake_quote)

        confirmation.build_confirmation_sections("fedex_label", _fields(customer_id), db)
        confirmation.build_confirmation_sections("ups_label", _fields(customer_id), db)
        assert seen_channels == ["fedex_ch", "ups_ch"]
    finally:
        _cleanup(db, customer_id)
