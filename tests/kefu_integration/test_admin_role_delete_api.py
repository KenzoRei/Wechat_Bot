"""
Real Postgres coverage for DELETE /admin/roles/{role_id}: protected role
names (admin, pending) can never be removed regardless of assignment, and
a role currently assigned to any group_member or kefu_staff row is
rejected with a clear 409 rather than a raw FK IntegrityError. See
core/role_registry.py's PROTECTED_ROLE_NAMES docstring for why "pending"
is protected even though nothing ever holds it permanently.
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import roles as roles_module
from database import SessionLocal, get_db

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _client():
    app = FastAPI()
    app.include_router(roles_module.router)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _headers():
    return {"X-Admin-Key": config.ADMIN_API_KEY}


def _real_group_id(db):
    from models.group import GroupConfig
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _make_role(db, name=None):
    from models.role import Role
    role = Role(name=name or f"t_delrole_{uuid.uuid4().hex[:8]}", description="test-owned")
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _cleanup_role(role_id):
    db = SessionLocal()
    try:
        db.execute(text("delete from role where role_id = :rid"), {"rid": role_id})
        db.commit()
    finally:
        db.close()


def test_deleting_an_unassigned_role_succeeds():
    db = SessionLocal()
    try:
        role = _make_role(db)
        role_id = str(role.role_id)
    finally:
        db.close()

    resp = _client().delete(f"/admin/roles/{role_id}", headers=_headers())
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        from models.role import Role
        assert db.query(Role).filter_by(role_id=role_id).first() is None
    finally:
        db.close()


def test_deleting_admin_role_is_rejected():
    db = SessionLocal()
    try:
        from models.role import Role
        admin_role = db.query(Role).filter_by(name="admin").first()
        assert admin_role is not None, "admin role missing -- seed data broken"
        role_id = str(admin_role.role_id)
    finally:
        db.close()

    resp = _client().delete(f"/admin/roles/{role_id}", headers=_headers())
    assert resp.status_code == 409
    assert "protected" in resp.json()["detail"].lower()


def test_deleting_pending_role_is_rejected_even_though_unassigned():
    db = SessionLocal()
    try:
        from models.role import Role
        pending_role = db.query(Role).filter_by(name="pending").first()
        assert pending_role is not None, "pending role missing -- migration V7 not applied?"
        role_id = str(pending_role.role_id)
    finally:
        db.close()

    resp = _client().delete(f"/admin/roles/{role_id}", headers=_headers())
    assert resp.status_code == 409
    assert "protected" in resp.json()["detail"].lower()


def test_deleting_a_role_assigned_to_a_group_member_is_rejected():
    from models.group import GroupMember

    db = SessionLocal()
    try:
        role = _make_role(db)
        role_id = role.role_id
        group_id = _real_group_id(db)
        openid = f"t-delrole-member-{uuid.uuid4().hex[:8]}"
        db.add(GroupMember(wechat_openid=openid, group_id=group_id, role_id=role_id, display_name="test"))
        db.commit()
    finally:
        db.close()

    try:
        resp = _client().delete(f"/admin/roles/{role_id}", headers=_headers())
        assert resp.status_code == 409
        assert "assigned to" in resp.json()["detail"]
    finally:
        db2 = SessionLocal()
        try:
            db2.execute(text("delete from group_member where wechat_openid = :o"), {"o": openid})
            db2.commit()
        finally:
            db2.close()
        _cleanup_role(role_id)


def test_deleting_a_role_assigned_to_kefu_staff_is_rejected():
    from models.kefu import KefuStaff

    db = SessionLocal()
    try:
        role = _make_role(db)
        role_id = role.role_id
        group_id = _real_group_id(db)
        open_kfid = f"kf-delrole-{uuid.uuid4().hex[:8]}"
        external_userid = f"staff-delrole-{uuid.uuid4().hex[:8]}"
        db.add(KefuStaff(open_kfid=open_kfid, external_userid=external_userid, group_id=group_id, role_id=role_id))
        db.commit()
    finally:
        db.close()

    try:
        resp = _client().delete(f"/admin/roles/{role_id}", headers=_headers())
        assert resp.status_code == 409
        assert "assigned to" in resp.json()["detail"]
    finally:
        db2 = SessionLocal()
        try:
            db2.execute(text("delete from kefu_staff where open_kfid = :k and external_userid = :e"),
                        {"k": open_kfid, "e": external_userid})
            db2.commit()
        finally:
            db2.close()
        _cleanup_role(role_id)


def test_deleting_a_nonexistent_role_returns_404():
    resp = _client().delete(f"/admin/roles/{uuid.uuid4()}", headers=_headers())
    assert resp.status_code == 404
