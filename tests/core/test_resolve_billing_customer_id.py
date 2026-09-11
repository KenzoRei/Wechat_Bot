"""
Direct coverage for core.customer_directory.resolve_billing_customer_id --
the authorization-relevant piece shared by both channels' turn-processing
pipelines (core/kefu_turn_apply.py, core/workflow_engine.py) and revalidated
by handlers/label/base.py at confirmation time. See
docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
§3.8.
"""
import uuid

from sqlalchemy import text

from database import SessionLocal
from core import customer_directory as cd


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def test_requester_own_identity_is_authoritative_even_if_extracted_differs():
    """A customer-role requester's own binding always wins -- even if the
    conversation somehow extracted a different id, it must be ignored."""
    db = SessionLocal()
    own_id, other_id = _fresh_customer_id(), _fresh_customer_id()
    try:
        cd.upsert_customer(db, own_id, actor="test", display_name="Own Co", status="active")
        cd.upsert_customer(db, other_id, actor="test", display_name="Other Co", status="active")

        resolved, error = cd.resolve_billing_customer_id(db, True, own_id, other_id)
        assert error is None
        assert resolved == own_id
    finally:
        db.execute(text("delete from customer where customer_id in (:a, :b)"), {"a": own_id, "b": other_id})
        db.commit()
        db.close()


def test_requester_own_identity_must_be_active():
    db = SessionLocal()
    own_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, own_id, actor="test", display_name="Suspended Co", status="inactive")
        resolved, error = cd.resolve_billing_customer_id(db, True, own_id, None)
        assert resolved is None
        assert error is not None
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": own_id})
        db.commit()
        db.close()


def test_unbound_customer_role_is_rejected_not_treated_as_staff():
    """The core fix for the reported gap: a customer-role caller with no
    binding of their own must be REJECTED outright -- never fall through to
    the staff path and let them pick an arbitrary active customer just by
    supplying its F###### code."""
    db = SessionLocal()
    other_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, other_id, actor="test", display_name="Someone Else Co", status="active")
        # is_customer_role=True, no binding of their own, but they supplied
        # (or the AI extracted) a real, active, unrelated customer's id.
        resolved, error = cd.resolve_billing_customer_id(db, True, None, other_id)
        assert resolved is None
        assert error is not None
        assert other_id not in error  # rejected on the caller's own missing binding, not a complaint about other_id
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": other_id})
        db.commit()
        db.close()


def test_no_requester_identity_no_extracted_value_returns_not_yet_provided():
    """Missing-field wait, not a rejection -- staff/non-customer path only."""
    db = SessionLocal()
    try:
        resolved, error = cd.resolve_billing_customer_id(db, False, None, None)
        assert resolved is None
        assert error is None
    finally:
        db.close()


def test_staff_path_validates_extracted_value_exists():
    db = SessionLocal()
    try:
        resolved, error = cd.resolve_billing_customer_id(db, False, None, "F999999")
        assert resolved is None
        assert error is not None
        assert "F999999" in error
    finally:
        db.close()


def test_staff_path_validates_extracted_value_is_active():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Pending Co", status="pending")
        resolved, error = cd.resolve_billing_customer_id(db, False, None, customer_id)
        assert resolved is None
        assert error is not None
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_staff_path_accepts_valid_active_extracted_value():
    db = SessionLocal()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Active Co", status="active")
        resolved, error = cd.resolve_billing_customer_id(db, False, None, customer_id)
        assert error is None
        assert resolved == customer_id
    finally:
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()
