"""
Real Postgres coverage for the billing_customer_id gap Codex found: a
customer-role GroupMember had no admin-side way to actually be bound to a
real customer, which meant core.customer_directory.resolve_billing_customer_id
was silently letting an unbound "customer" fall through and pick any
active customer's billing account. This exercises the actual admin write
path (api/admin/members.py) that closes that gap.
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import members as members_module
from database import SessionLocal, get_db
from models.group import GroupConfig
from models.role import Role
from core import customer_directory as cd

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _client():
    app = FastAPI()
    app.include_router(members_module.router)

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


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return str(group.group_id)


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _fresh_openid() -> str:
    return f"test-openid-{uuid.uuid4().hex[:12]}"


def test_create_customer_role_member_requires_billing_customer_id():
    client = _client()
    db = SessionLocal()
    group_id = _real_group_id(db)
    db.close()
    openid = _fresh_openid()
    try:
        resp = client.post(f"/admin/groups/{group_id}/members", headers=_headers(), json={
            "wechat_openid": openid, "role": "customer",
        })
        assert resp.status_code == 400
        assert "billing_customer_id" in resp.text
    finally:
        db = SessionLocal()
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.commit()
        db.close()


def test_create_customer_role_member_rejects_unknown_customer():
    client = _client()
    db = SessionLocal()
    group_id = _real_group_id(db)
    db.close()
    openid = _fresh_openid()
    try:
        resp = client.post(f"/admin/groups/{group_id}/members", headers=_headers(), json={
            "wechat_openid": openid, "role": "customer", "billing_customer_id": "F999999",
        })
        assert resp.status_code == 400
        assert "F999999" in resp.text
    finally:
        db = SessionLocal()
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.commit()
        db.close()


def test_create_customer_role_member_succeeds_with_valid_active_customer():
    client = _client()
    db = SessionLocal()
    group_id = _real_group_id(db)
    customer_id = _fresh_customer_id()
    cd.upsert_customer(db, customer_id, actor="test", display_name="Bound Co", status="active")
    db.close()
    openid = _fresh_openid()
    try:
        resp = client.post(f"/admin/groups/{group_id}/members", headers=_headers(), json={
            "wechat_openid": openid, "role": "customer", "billing_customer_id": customer_id,
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["billing_customer_id"] == customer_id

        # The actual gap this closes: the requester's own live binding is
        # now real, so resolve_billing_customer_id's authoritative-identity
        # path has something genuine to read instead of always being None.
        db2 = SessionLocal()
        from models.group import GroupMember
        member = db2.get(GroupMember, {"wechat_openid": openid, "group_id": group_id})
        assert member.billing_customer_id == customer_id
        db2.close()
    finally:
        db = SessionLocal()
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.execute(text("delete from customer where customer_id = :c"), {"c": customer_id})
        db.commit()
        db.close()


def test_billing_customer_id_cleared_when_role_changes_away_from_customer():
    client = _client()
    db = SessionLocal()
    group_id = _real_group_id(db)
    customer_id = _fresh_customer_id()
    cd.upsert_customer(db, customer_id, actor="test", display_name="Reassigned Co", status="active")
    db.close()
    openid = _fresh_openid()
    try:
        create_resp = client.post(f"/admin/groups/{group_id}/members", headers=_headers(), json={
            "wechat_openid": openid, "role": "customer", "billing_customer_id": customer_id,
        })
        assert create_resp.status_code == 201

        update_resp = client.patch(f"/admin/groups/{group_id}/members/{openid}", headers=_headers(), json={
            "role": "accountant",
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["billing_customer_id"] is None
    finally:
        db = SessionLocal()
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.execute(text("delete from customer where customer_id = :c"), {"c": customer_id})
        db.commit()
        db.close()


def test_billing_customer_id_rejected_for_non_customer_role():
    client = _client()
    db = SessionLocal()
    group_id = _real_group_id(db)
    customer_id = _fresh_customer_id()
    cd.upsert_customer(db, customer_id, actor="test", display_name="Wrong Role Co", status="active")
    db.close()
    openid = _fresh_openid()
    try:
        create_resp = client.post(f"/admin/groups/{group_id}/members", headers=_headers(), json={
            "wechat_openid": openid, "role": "accountant",
        })
        assert create_resp.status_code == 201

        update_resp = client.patch(f"/admin/groups/{group_id}/members/{openid}", headers=_headers(), json={
            "billing_customer_id": customer_id,
        })
        assert update_resp.status_code == 400
        assert "billing_customer_id only applies to role=customer" in update_resp.text
    finally:
        db = SessionLocal()
        db.execute(text("delete from group_member where wechat_openid = :o and group_id = :g"), {"o": openid, "g": group_id})
        db.execute(text("delete from customer where customer_id = :c"), {"c": customer_id})
        db.commit()
        db.close()
