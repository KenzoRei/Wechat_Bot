"""
Real Postgres, end-to-end coverage for handlers/uchoice/role_change.py's
billing_customer_id handling -- the exact gap Codex reported and reproduced:

* staff -> customer succeeded without a binding, leaving the member unable
  to create labels (rejected later by core.customer_directory.
  resolve_billing_customer_id as an unbound customer-role caller).
* customer -> staff retained the previous binding.

Mirrors the exact 3-layer pattern already proven for warehouse_codes/
warehouseman on this same handler (core/uchoice_field_sanitization.py,
core/pre_confirm_validators.py, handlers/uchoice/role_change.py) -- this
file exercises the real end-to-end handler + real customer/GroupMember
rows, since the offline mock-DB tests (test_role_change_hardening.py,
test_role_change_kefu_identity.py) can't reach core.customer_directory's
real DB lookups.
"""
import uuid

from database import SessionLocal
from models.group import GroupConfig, GroupMember
from models.role import Role
from core import customer_directory as cd
from handlers.uchoice.role_change import RoleChangeHandler

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return str(group.group_id)


def _real_role_id(db, name: str) -> str:
    return str(db.query(Role).filter_by(name=name).one().role_id)


def _fresh_openid() -> str:
    return f"test-rc-openid-{uuid.uuid4().hex[:12]}"


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _make_member(db, group_id, role_id, openid):
    member = GroupMember(wechat_openid=openid, group_id=group_id, role_id=role_id)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def test_staff_to_customer_requires_billing_customer_id():
    """Reproduces Codex's first finding: this must now be REJECTED, not
    silently succeed and leave the member unable to create labels."""
    db = SessionLocal()
    group_id = _real_group_id(db)
    accountant_role_id = _real_role_id(db, "accountant")
    openid = _fresh_openid()
    try:
        _make_member(db, group_id, accountant_role_id, openid)

        context = {
            "collected_fields": {"target_openid": openid, "new_role": "customer"},
            "group_id": group_id,
        }
        try:
            RoleChangeHandler().handle(context, {}, db)
            assert False, "expected RuntimeError for missing billing_customer_id"
        except RuntimeError as exc:
            assert "客户编号" in str(exc)

        db.rollback()
        member = db.query(GroupMember).filter_by(wechat_openid=openid, group_id=group_id).one()
        assert member.billing_customer_id is None
    finally:
        db.rollback()
        from sqlalchemy import text
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.commit()
        db.close()


def test_staff_to_customer_succeeds_with_valid_billing_customer_id():
    db = SessionLocal()
    group_id = _real_group_id(db)
    accountant_role_id = _real_role_id(db, "accountant")
    openid = _fresh_openid()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="RC Bind Co", status="active")
        _make_member(db, group_id, accountant_role_id, openid)

        context = {
            "collected_fields": {"target_openid": openid, "new_role": "customer", "billing_customer_id": customer_id},
            "group_id": group_id,
        }
        result = RoleChangeHandler().handle(context, {}, db)
        assert result == {"target_openid": openid, "new_role": "customer"}

        member = db.query(GroupMember).filter_by(wechat_openid=openid, group_id=group_id).one()
        assert member.billing_customer_id == customer_id
    finally:
        from sqlalchemy import text
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.execute(text("delete from customer where customer_id = :c"), {"c": customer_id})
        db.commit()
        db.close()


def test_customer_to_staff_clears_stale_billing_customer_id():
    """Reproduces Codex's second finding: the OLD binding must not survive
    a reassignment away from role=customer."""
    db = SessionLocal()
    group_id = _real_group_id(db)
    customer_role_id = _real_role_id(db, "customer")
    openid = _fresh_openid()
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="RC Clear Co", status="active")
        member = _make_member(db, group_id, customer_role_id, openid)
        member.billing_customer_id = customer_id
        db.commit()

        context = {
            "collected_fields": {"target_openid": openid, "new_role": "accountant"},
            "group_id": group_id,
        }
        result = RoleChangeHandler().handle(context, {}, db)
        assert result == {"target_openid": openid, "new_role": "accountant"}

        db.refresh(member)
        assert member.billing_customer_id is None
    finally:
        from sqlalchemy import text
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.execute(text("delete from customer where customer_id = :c"), {"c": customer_id})
        db.commit()
        db.close()


def test_kefu_target_rejected_for_customer_role_end_to_end():
    """KefuStaff has no billing_customer_id column -- must be rejected
    outright at the real handler, not just the mock-DB pre-confirm tests."""
    from models.kefu import KefuStaff
    from core.role_identity import tag_kefu_identity

    db = SessionLocal()
    group_id = _real_group_id(db)
    accountant_role_id = _real_role_id(db, "accountant")
    staff = KefuStaff(
        open_kfid=f"kf-rc-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-rc-{uuid.uuid4().hex[:8]}",
        group_id=group_id, role_id=accountant_role_id,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    try:
        context = {
            "collected_fields": {"target_openid": tag_kefu_identity(staff.staff_id), "new_role": "customer"},
            "group_id": group_id,
        }
        try:
            RoleChangeHandler().handle(context, {}, db)
            assert False, "expected RuntimeError for kefu target + customer role"
        except RuntimeError as exc:
            assert "客服账号" in str(exc)
    finally:
        from sqlalchemy import text
        db.execute(text("delete from kefu_staff where staff_id = :s"), {"s": staff.staff_id})
        db.commit()
        db.close()
