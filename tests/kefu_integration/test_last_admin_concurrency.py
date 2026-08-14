"""
Real concurrency coverage for the last-admin invariant (Codex round-4
review of the signed cross-review plan's implementation): proves
lock_group_admin_invariant actually serializes at the PostgreSQL level,
not just "the code path calls it." Each side of every test opens its own
real database connection via SessionLocal() and runs in its own OS thread,
synchronized with a threading.Barrier so both threads reach the advisory
lock at nearly the same instant -- whichever wins genuinely blocks the
other inside pg_advisory_xact_lock, not merely "happens to run second."

Covers both gaps the review named explicitly:
- chat-vs-chat: two concurrent RoleChangeHandler demotions in one group.
- REST-vs-chat: one REST kefu_staff PATCH racing one RoleChangeHandler
  demotion in the same group.

Same production-database isolation discipline as the rest of this
directory (test_kefu_admin_purge.py's docstring): all rows are created and
cleaned up scoped to this test's own IDs.
"""
import threading
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import kefu_staff as kefu_staff_module
from database import SessionLocal, get_db
from handlers.uchoice.role_change import RoleChangeHandler
from core.role_identity import tag_kefu_identity
from models.group import GroupConfig, GroupMember
from models.kefu import KefuStaff
from models.role import Role

AUTH = {"X-Admin-Key": config.ADMIN_API_KEY}


@pytest.fixture
def rest_client():
    app = FastAPI()
    app.include_router(kefu_staff_module.router)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _make_group_member_admin(db, group_id, admin_role_id):
    member = GroupMember(
        wechat_openid=f"race-member-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=admin_role_id,
        is_active=True,
        joined_at=datetime.now(timezone.utc),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _make_kefu_staff_admin(db, group_id, admin_role_id):
    staff = KefuStaff(
        open_kfid=f"kf-race-{uuid.uuid4().hex[:8]}",
        external_userid=f"race-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=admin_role_id,
        is_active=True,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _count_active_admins(group_id, admin_role_id):
    db = SessionLocal()
    try:
        member_count = db.query(GroupMember).filter_by(
            group_id=group_id, role_id=admin_role_id, is_active=True
        ).count()
        staff_count = db.query(KefuStaff).filter_by(
            group_id=group_id, role_id=admin_role_id, is_active=True
        ).count()
        return member_count + staff_count
    finally:
        db.close()


def test_chat_vs_chat_concurrent_demotion_never_reaches_zero_admins():
    setup_db = SessionLocal()
    member_id = None
    staff_id = None
    try:
        group = setup_db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = setup_db.query(Role).filter_by(name="admin").one()

        member = _make_group_member_admin(setup_db, group.group_id, admin_role.role_id)
        staff = _make_kefu_staff_admin(setup_db, group.group_id, admin_role.role_id)
        member_id, staff_id = member.wechat_openid, staff.staff_id

        barrier = threading.Barrier(2)
        results = {}

        def _demote(label: str, target_openid: str):
            db = SessionLocal()
            try:
                context = {
                    "collected_fields": {
                        "target_openid": target_openid,
                        "new_role": "accountant",
                        "warehouse_code": None,
                    },
                    "group_id": group.group_id,
                }
                barrier.wait(timeout=10)
                try:
                    RoleChangeHandler().handle(context, {}, db)
                    results[label] = "success"
                except RuntimeError as exc:
                    db.rollback()
                    results[label] = f"rejected: {exc}"
            finally:
                db.close()

        t1 = threading.Thread(target=_demote, args=("member", member_id))
        t2 = threading.Thread(target=_demote, args=("staff", tag_kefu_identity(staff_id)))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert set(results.keys()) == {"member", "staff"}
        outcomes = list(results.values())
        successes = [o for o in outcomes if o == "success"]
        rejections = [o for o in outcomes if o.startswith("rejected")]
        assert len(successes) == 1, f"expected exactly one demotion to succeed, got: {results}"
        assert len(rejections) == 1, f"expected exactly one demotion rejected, got: {results}"
        assert "仅剩一名管理员" in rejections[0]

        assert _count_active_admins(group.group_id, admin_role.role_id) == 1, (
            "the group must never end up with zero active admins"
        )
    finally:
        setup_db.rollback()
        if member_id:
            setup_db.execute(text("delete from group_member where wechat_openid=:oid"), {"oid": member_id})
        if staff_id:
            setup_db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        setup_db.commit()
        setup_db.close()


def test_rest_vs_chat_concurrent_demotion_never_reaches_zero_admins(rest_client):
    setup_db = SessionLocal()
    member_id = None
    staff_id = None
    try:
        group = setup_db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = setup_db.query(Role).filter_by(name="admin").one()

        member = _make_group_member_admin(setup_db, group.group_id, admin_role.role_id)
        staff = _make_kefu_staff_admin(setup_db, group.group_id, admin_role.role_id)
        member_id, staff_id = member.wechat_openid, staff.staff_id

        barrier = threading.Barrier(2)
        results = {}

        def _chat_demote_member():
            db = SessionLocal()
            try:
                context = {
                    "collected_fields": {
                        "target_openid": member_id,
                        "new_role": "accountant",
                        "warehouse_code": None,
                    },
                    "group_id": group.group_id,
                }
                barrier.wait(timeout=10)
                try:
                    RoleChangeHandler().handle(context, {}, db)
                    results["chat"] = "success"
                except RuntimeError as exc:
                    db.rollback()
                    results["chat"] = f"rejected: {exc}"
            finally:
                db.close()

        def _rest_demote_staff():
            barrier.wait(timeout=10)
            resp = rest_client.patch(
                f"/admin/kefu-staff/{staff_id}", json={"role": "accountant"}, headers=AUTH
            )
            results["rest"] = "success" if resp.status_code == 200 else f"rejected: {resp.status_code}"

        t1 = threading.Thread(target=_chat_demote_member)
        t2 = threading.Thread(target=_rest_demote_staff)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert set(results.keys()) == {"chat", "rest"}
        outcomes = list(results.values())
        successes = [o for o in outcomes if o == "success"]
        rejections = [o for o in outcomes if o.startswith("rejected")]
        assert len(successes) == 1, f"expected exactly one demotion to succeed, got: {results}"
        assert len(rejections) == 1, f"expected exactly one demotion rejected, got: {results}"

        assert _count_active_admins(group.group_id, admin_role.role_id) == 1, (
            "the group must never end up with zero active admins across the REST and chat paths"
        )
    finally:
        setup_db.rollback()
        if member_id:
            setup_db.execute(text("delete from group_member where wechat_openid=:oid"), {"oid": member_id})
        if staff_id:
            setup_db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        setup_db.commit()
        setup_db.close()
