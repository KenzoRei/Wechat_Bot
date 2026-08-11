"""
Real-Postgres tests for customer selection/locking candidate injection
(kefu-migration-plan.md Sec 6.2, discussion.md round 98). A mock context
dict can't prove this -- it's about what session_manager.build_context()
actually queries and scopes against the real uchoice_customer/uchoice_address
tables, and how that changes once a real ConversationSession has
session.customer_id set.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from core import access_control, session_manager

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db):
    from models.group import GroupConfig
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _real_role_id(db, name="admin"):
    from models.role import Role
    role = db.query(Role).filter_by(name=name).first()
    assert role is not None, f"'{name}' role not found -- seed data missing"
    return role.role_id


@pytest.fixture
def kefu_access():
    """A real AccessResult for a fresh admin Kefu staff member (granted
    uchoice_inbound_request/uchoice_outbound_request/upsert_address, all
    customer-scoped per Sec 6.2)."""
    db = SessionLocal()
    from models.kefu import KefuStaff
    staff = KefuStaff(
        open_kfid=f"kf-custsel-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-custsel-{uuid.uuid4().hex[:8]}",
        group_id=_real_group_id(db),
        role_id=_real_role_id(db),
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
    assert isinstance(access, access_control.AccessResult)
    names = {s["name"] for s in access.allowed_services}
    assert {"uchoice_inbound_request", "uchoice_outbound_request", "upsert_address"} <= names, (
        "fixture admin role must be granted every customer-scoped service for this test"
    )
    staff_id = staff.staff_id
    try:
        yield access, db
    finally:
        db.execute(text("delete from kefu_staff where staff_id = :sid"), {"sid": staff_id})
        db.commit()
        db.close()


def test_customer_candidates_injected_before_lock_and_withheld_after(kefu_access):
    access, db = kefu_access

    message = {"content": "帮我提交一个申请", "msg_id": "", "response_url": ""}
    context = session_manager.build_context(db, access, session=None, message=message)

    candidates = context["uchoice_candidates"]
    assert "customers" in candidates, "customer candidate list must be injected before any customer is locked"
    assert len(candidates["customers"]) > 0, "fixture uchoice_customer directory must be non-empty"
    # Real active directory rows, not a placeholder -- every entry has a
    # real customer_id/canonical_name.
    for c in candidates["customers"]:
        assert c["customer_id"]
        assert c["canonical_name"]

    # Before a customer is locked, addresses must be WITHHELD entirely for
    # Kefu (not just unfiltered) -- otherwise every other customer's
    # addresses would leak into the AI's prompt before the case even knows
    # who it's for.
    assert "addresses" not in candidates, "addresses must not leak before the case's customer is locked"

    assert context["customer_id"] is None


def test_addresses_scoped_to_locked_customer_once_session_has_one(kefu_access):
    access, db = kefu_access
    from models.uchoice import UchoiceAddress
    from models.kefu import UchoiceCustomer
    from models.session import ConversationSession
    from datetime import datetime, timedelta, timezone

    # Two distinct customers, each with their own address, so cross-leakage
    # is directly observable rather than inferred.
    cust_a = UchoiceCustomer(customer_code=f"TESTA-{uuid.uuid4().hex[:6]}", canonical_name="Test Customer A")
    cust_b = UchoiceCustomer(customer_code=f"TESTB-{uuid.uuid4().hex[:6]}", canonical_name="Test Customer B")
    db.add_all([cust_a, cust_b])
    db.commit()
    db.refresh(cust_a)
    db.refresh(cust_b)

    addr_a = UchoiceAddress(
        company_name="Customer A Co", charge_type="delivery", addr="1 A St",
        warehouse_code="JFK", created_by="test", customer_id=cust_a.customer_id,
    )
    addr_b = UchoiceAddress(
        company_name="Customer B Co", charge_type="delivery", addr="2 B St",
        warehouse_code="JFK", created_by="test", customer_id=cust_b.customer_id,
    )
    db.add_all([addr_a, addr_b])
    db.commit()

    session = ConversationSession(
        wechat_openid=None, group_id=access.group_id, status="active",
        conversation_history=[], collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        source_channel="kefu", customer_id=cust_a.customer_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    try:
        message = {"content": "查一下地址", "msg_id": "", "response_url": ""}
        context = session_manager.build_context(db, access, session=session, message=message)
        candidates = context["uchoice_candidates"]

        assert context["customer_id"] == str(cust_a.customer_id)
        assert "customers" not in candidates, "customer list must not reappear once locked"
        addr_ids = {a["address_id"] for a in candidates.get("addresses", [])}
        assert str(addr_a.address_id) in addr_ids, "the locked customer's own address must be visible"
        assert str(addr_b.address_id) not in addr_ids, "a different customer's address must never leak in"
    finally:
        db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session.session_id})
        db.execute(text("delete from uchoice_address where address_id in (:a, :b)"), {"a": addr_a.address_id, "b": addr_b.address_id})
        db.execute(text("delete from uchoice_customer where customer_id in (:a, :b)"), {"a": cust_a.customer_id, "b": cust_b.customer_id})
        db.commit()
