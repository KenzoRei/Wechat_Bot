"""
Real-Postgres tests for the shared U-Choice address book
(kefu-deterministic-response-plan.md Sec 5). Distinct from
test_kefu_customer_selection.py, which covers the customer-lock interaction;
this file covers address-sharing mechanics on their own: null/varied
customer_id are all matchable, upsert_address gets the same candidate list
as uchoice_outbound_request (a real, previously-missing gap), and a service
outside the authorized set receives no address list at all.
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
    db = SessionLocal()
    from models.kefu import KefuStaff
    staff = KefuStaff(
        open_kfid=f"kf-sharedaddr-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-sharedaddr-{uuid.uuid4().hex[:8]}",
        group_id=_real_group_id(db),
        role_id=_real_role_id(db),
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
    assert isinstance(access, access_control.AccessResult)
    staff_id = staff.staff_id
    try:
        yield access, db
    finally:
        db.execute(text("delete from kefu_staff where staff_id = :sid"), {"sid": staff_id})
        db.commit()
        db.close()


def test_addresses_with_varied_and_null_customer_id_are_all_matchable(kefu_access):
    access, db = kefu_access
    from models.uchoice import UchoiceAddress
    from models.kefu import UchoiceCustomer

    cust = UchoiceCustomer(customer_code=f"SHARED-{uuid.uuid4().hex[:6]}", canonical_name="Shared Test Customer")
    db.add(cust)
    db.commit()
    db.refresh(cust)

    addr_with_customer = UchoiceAddress(
        company_name="Has Customer Co", charge_type="delivery", addr="1 Has St",
        warehouse_code="JFK", created_by="test", customer_id=cust.customer_id,
    )
    addr_without_customer = UchoiceAddress(
        company_name="No Customer Co", charge_type="delivery", addr="2 None St",
        warehouse_code="JFK", created_by="test", customer_id=None,
    )
    db.add_all([addr_with_customer, addr_without_customer])
    db.commit()

    try:
        message = {"content": "帮我出库", "msg_id": "", "response_url": ""}
        # No session yet -- no customer could possibly be locked.
        context = session_manager.build_context(db, access, session=None, message=message)
        addr_ids = {a["address_id"] for a in context["uchoice_candidates"].get("addresses", [])}

        assert str(addr_with_customer.address_id) in addr_ids
        assert str(addr_without_customer.address_id) in addr_ids
    finally:
        db.execute(text("delete from uchoice_address where address_id in (:a, :b)"), {
            "a": addr_with_customer.address_id, "b": addr_without_customer.address_id,
        })
        db.execute(text("delete from uchoice_customer where customer_id = :c"), {"c": cust.customer_id})
        db.commit()


def test_upsert_address_receives_the_same_shared_candidate_list(kefu_access):
    """
    Previously a real gap: only uchoice_outbound_request triggered address
    candidate injection, so upsert_address's own matched_address_id
    create-vs-update resolution had nothing to match against. Sec 5.1
    requires both.
    """
    access, db = kefu_access
    names = {s["name"] for s in access.allowed_services}
    assert "upsert_address" in names, "fixture admin role must be granted upsert_address for this test"

    message = {"content": "更新地址", "msg_id": "", "response_url": ""}
    context = session_manager.build_context(db, access, session=None, message=message)
    assert "addresses" in context["uchoice_candidates"]


def test_service_outside_the_authorized_address_using_set_gets_no_address_list(kefu_access):
    access, db = kefu_access
    names = {s["name"] for s in access.allowed_services}
    # view_storage never matches addresses -- if this fixture role somehow
    # isn't granted it, the assertion below is meaningless, so require it.
    assert "view_storage" in names

    from models.session import ConversationSession
    from datetime import datetime, timedelta, timezone
    from models.service import ServiceType

    view_storage_type = db.query(ServiceType).filter_by(name="view_storage").first()
    session = ConversationSession(
        wechat_openid=None, group_id=access.group_id, status="active",
        conversation_history=[], collected_fields={}, service_type_id=view_storage_type.service_type_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        source_channel="kefu",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    try:
        message = {"content": "查库存", "msg_id": "", "response_url": ""}
        context = session_manager.build_context(db, access, session=session, message=message)
        # Session already has a locked-in service (view_storage), so
        # candidate scoping narrows to just that service's own needs --
        # view_storage never needs addresses.
        assert "addresses" not in context["uchoice_candidates"]
    finally:
        db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session.session_id})
        db.commit()
