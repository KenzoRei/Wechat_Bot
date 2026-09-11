"""
Real Postgres coverage for /admin/roles/{role_id}/services/*, the GLOBAL
role->service permission surface that replaced the old, per-group
/admin/groups/{group_id}/services/{service_type_id}/roles (group_service_role).
See models.role.RoleServicePermission's docstring for why this is no
longer per-group.
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import roles as roles_module
from database import SessionLocal, get_db


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


def _make_role_and_service():
    db = SessionLocal()
    try:
        from models.role import Role
        from models.service import ServiceType

        role = Role(name=f"t_perm_{uuid.uuid4().hex[:8]}", description="test-owned")
        db.add(role)
        db.commit()
        db.refresh(role)

        service = db.query(ServiceType).order_by(ServiceType.name).first()
        assert service is not None, "no service_type rows -- seed data missing"
        return str(role.role_id), str(service.service_type_id), service.name
    finally:
        db.close()


def _cleanup_role(role_id: str):
    db = SessionLocal()
    try:
        db.execute(text("delete from role where role_id = :rid"), {"rid": role_id})
        db.commit()
    finally:
        db.close()


def test_grant_list_revoke_role_service_permission():
    client = _client()
    role_id, service_type_id, service_name = _make_role_and_service()
    try:
        grant_resp = client.post(
            f"/admin/roles/{role_id}/services/{service_type_id}",
            headers=_headers(), json={"created_by": "test_admin"},
        )
        assert grant_resp.status_code == 201, grant_resp.text
        data = grant_resp.json()["data"]
        assert data["role_id"] == role_id
        assert data["service_type_id"] == service_type_id
        assert data["service_name"] == service_name

        list_resp = client.get(f"/admin/roles/{role_id}/services", headers=_headers())
        assert list_resp.status_code == 200
        assert any(g["service_type_id"] == service_type_id for g in list_resp.json()["data"])

        revoke_resp = client.delete(f"/admin/roles/{role_id}/services/{service_type_id}", headers=_headers())
        assert revoke_resp.status_code == 200

        list_after = client.get(f"/admin/roles/{role_id}/services", headers=_headers())
        assert list_after.json()["data"] == []
    finally:
        _cleanup_role(role_id)


def test_grant_duplicate_conflicts():
    client = _client()
    role_id, service_type_id, _ = _make_role_and_service()
    try:
        body = {"created_by": "test_admin"}
        assert client.post(f"/admin/roles/{role_id}/services/{service_type_id}", headers=_headers(), json=body).status_code == 201
        dup = client.post(f"/admin/roles/{role_id}/services/{service_type_id}", headers=_headers(), json=body)
        assert dup.status_code == 409
    finally:
        _cleanup_role(role_id)


def test_grant_unknown_role_404s():
    client = _client()
    role_id, service_type_id, _ = _make_role_and_service()
    try:
        resp = client.post(
            f"/admin/roles/{uuid.uuid4()}/services/{service_type_id}",
            headers=_headers(), json={"created_by": "test_admin"},
        )
        assert resp.status_code == 404
    finally:
        _cleanup_role(role_id)


def test_grant_unknown_service_404s():
    client = _client()
    role_id, _, _ = _make_role_and_service()
    try:
        resp = client.post(
            f"/admin/roles/{role_id}/services/{uuid.uuid4()}",
            headers=_headers(), json={"created_by": "test_admin"},
        )
        assert resp.status_code == 404
    finally:
        _cleanup_role(role_id)


def test_revoke_nonexistent_grant_404s():
    client = _client()
    role_id, service_type_id, _ = _make_role_and_service()
    try:
        resp = client.delete(f"/admin/roles/{role_id}/services/{service_type_id}", headers=_headers())
        assert resp.status_code == 404
    finally:
        _cleanup_role(role_id)


def test_permission_is_global_not_per_group():
    """The core behavior change this migration made: a grant has no
    group_id at all, and applies identically regardless of which group a
    caller belongs to (real tenant differentiation still lives in
    GroupService, untouched by this)."""
    client = _client()
    role_id, service_type_id, _ = _make_role_and_service()
    try:
        client.post(f"/admin/roles/{role_id}/services/{service_type_id}", headers=_headers(), json={"created_by": "test_admin"})
        db = SessionLocal()
        try:
            from models.role import RoleServicePermission
            row = db.get(RoleServicePermission, (role_id, service_type_id))
            assert row is not None
            assert not hasattr(row, "group_id")
        finally:
            db.close()
    finally:
        _cleanup_role(role_id)
