"""
Real Postgres coverage for core/customer_directory.py -- the module boundary
for the new customer master-data tables (customer, customer_credential).
Fail-closed, exact-row cleanup.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from core import customer_directory as cd


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def test_upsert_and_get_customer_round_trip():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        created = cd.upsert_customer(
            db, customer_id, actor="test_customer_directory",
            display_name="Test Co", status="active",
        )
        assert created.customer_id == customer_id
        assert created.display_name == "Test Co"
        assert created.rate_multiplier == {}
        assert created.toggles == {}

        fetched = cd.get_customer(db, customer_id)
        assert fetched is not None
        assert fetched.display_name == "Test Co"

        updated = cd.upsert_customer(db, customer_id, actor="test_customer_directory", display_name="Test Co Updated")
        assert updated.display_name == "Test Co Updated"
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_get_customer_returns_none_for_unknown_id():
    db = SessionLocal()
    try:
        assert cd.get_customer(db, "F999999") is None
    finally:
        db.close()


def test_margin_and_toggle_read_from_jsonb_dicts():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(
            db, customer_id, actor="test_customer_directory",
            display_name="Margin Test Co",
            rate_multiplier={"fedex": 1.3, "ups": 1.25},
            toggles={"ups_incentive": True},
        )
        from decimal import Decimal
        assert cd.get_margin(db, customer_id, "fedex") == Decimal("1.3")
        assert cd.get_margin(db, customer_id, "usps") is None
        assert cd.get_toggle(db, customer_id, "ups_incentive") is True
        assert cd.get_toggle(db, customer_id, "unknown_toggle", default=False) is False
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_credential_round_trip_encrypts_at_rest_and_decrypts_correctly():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test_customer_directory", display_name="Credential Test Co")
        cd.set_credential(db, customer_id, "ydd_username", customer_id, actor="test_customer_directory")
        cd.set_credential(db, customer_id, "ydd_password", "s3cr3t-pw", actor="test_customer_directory")

        # Verify at-rest value is NOT plaintext (real encryption, not a no-op).
        raw = db.execute(
            text("select encrypted_value from customer_credential where customer_id = :cid and credential_type = 'ydd_password'"),
            {"cid": customer_id},
        ).scalar_one()
        assert b"s3cr3t-pw" not in bytes(raw)

        creds = cd.get_credentials(db, customer_id)
        assert creds == {"ydd_username": customer_id, "ydd_password": "s3cr3t-pw"}

        assert cd.has_credentials(db, customer_id, "ydd_username", "ydd_password") is True
        assert cd.has_credentials(db, customer_id, "oms_app_key", "oms_app_secret") is False

        # Overwrite must replace, not duplicate.
        cd.set_credential(db, customer_id, "ydd_password", "rotated-pw", actor="test_customer_directory")
        assert cd.get_credentials(db, customer_id)["ydd_password"] == "rotated-pw"
        count = db.execute(
            text("select count(*) from customer_credential where customer_id = :cid and credential_type = 'ydd_password'"),
            {"cid": customer_id},
        ).scalar_one()
        assert count == 1
    finally:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_set_credential_rejects_unknown_credential_type():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test_customer_directory", display_name="Reject Test Co")
        with pytest.raises(ValueError):
            cd.set_credential(db, customer_id, "not_a_real_type", "x", actor="test_customer_directory")
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()
