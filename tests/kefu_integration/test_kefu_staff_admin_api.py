"""
Route-level coverage for the privileged api/admin/kefu_staff.py write surface.
Runs the router
mounted standalone on a minimal FastAPI app (same isolation pattern as
tests/kefu/test_kefu_callback.py) against real Postgres, scoped to rows
this file creates itself (tests/kefu_integration/test_kefu_admin_purge.py's
docstring explains why: this suite runs directly against the shared
production database, no separate test DB).
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import kefu_staff as kefu_staff_module
from database import SessionLocal, get_db
from models.group import GroupConfig
from models.kefu import KefuStaff
from models.role import Role


@pytest.fixture
def client():
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


AUTH = {"X-Admin-Key": config.ADMIN_API_KEY}


def _make_staff(db, group_id, role_name, *, is_active=True, display_name=None, warehouse_code=None):
    role = db.query(Role).filter_by(name=role_name).one()
    staff = KefuStaff(
        open_kfid=f"kf-test-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=role.role_id,
        is_active=is_active,
        display_name=display_name,
        warehouse_code=warehouse_code,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _cleanup(db, staff_ids):
    if staff_ids:
        db.execute(text("delete from kefu_staff where staff_id=any(:ids)"), {"ids": staff_ids})
        db.commit()


def test_missing_admin_key_rejected(client):
    resp = client.get("/admin/kefu-staff")
    assert resp.status_code in (401, 422)  # 422 if the header dependency itself rejects a missing header


def test_wrong_admin_key_rejected(client):
    resp = client.get("/admin/kefu-staff", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 401


def test_list_and_pending_only_filter(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        pending = _make_staff(db, group.group_id, "pending", display_name="Pending Person")
        active = _make_staff(db, group.group_id, "accountant", display_name="Active Person")
        staff_ids = [pending.staff_id, active.staff_id]

        resp = client.get("/admin/kefu-staff", headers=AUTH)
        assert resp.status_code == 200
        ids_in_response = {row["staff_id"] for row in resp.json()["data"]}
        assert str(pending.staff_id) in ids_in_response
        assert str(active.staff_id) in ids_in_response

        resp = client.get("/admin/kefu-staff", params={"pending_only": True}, headers=AUTH)
        assert resp.status_code == 200
        pending_ids = {row["staff_id"] for row in resp.json()["data"]}
        assert str(pending.staff_id) in pending_ids
        assert str(active.staff_id) not in pending_ids
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_response_schema_fields(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "customer", display_name="Schema Check")
        staff_ids = [staff.staff_id]

        resp = client.get("/admin/kefu-staff", headers=AUTH)
        row = next(r for r in resp.json()["data"] if r["staff_id"] == str(staff.staff_id))
        for field in (
            "staff_id", "open_kfid", "external_userid", "group_id",
            "role", "display_name", "warehouse_code", "is_active", "created_at",
        ):
            assert field in row
        assert row["role"] == "customer"
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_assign_role_from_pending(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"role": "accountant"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "accountant"

        db.refresh(staff)
        role = db.query(Role).filter_by(role_id=staff.role_id).one()
        assert role.name == "accountant"
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_unknown_role_rejected(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"role": "definitely_not_a_role"}, headers=AUTH)
        assert resp.status_code == 400
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_pending_is_not_assignable_via_this_endpoint(client):
    """pending is the pre-assignment state, not a target -- ASSIGNABLE_ROLE_NAMES excludes it."""
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "accountant")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"role": "pending"}, headers=AUTH)
        assert resp.status_code == 400
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_warehouseman_requires_warehouse_code(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"role": "warehouseman"}, headers=AUTH)
        assert resp.status_code == 400
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_warehouseman_rejects_whitespace_only_code(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(
            f"/admin/kefu-staff/{staff.staff_id}",
            json={"role": "warehouseman", "warehouse_code": "   "},
            headers=AUTH,
        )
        assert resp.status_code == 400
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_warehouseman_with_valid_code_succeeds(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(
            f"/admin/kefu-staff/{staff.staff_id}",
            json={"role": "warehouseman", "warehouse_code": "JFK"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["warehouse_code"] == "JFK"
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_deactivate_non_admin_succeeds(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "accountant")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"is_active": False}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is False
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_last_admin_cannot_be_demoted_or_deactivated(client, monkeypatch):
    """
    Scopes the count check to a synthetic group_id via monkeypatch so this
    test's assertion holds regardless of how many real admins already exist
    in the shared production database -- same isolation approach as
    test_kefu_admin_purge.py's _scope_open_sessions_to.

    Patching core.admin_invariants.count_active_admins alone is enough:
    would_remove_last_admin's body always resolves count_active_admins via
    its own defining module's globals at call time, regardless of which
    module (api/admin/kefu_staff.py) imported would_remove_last_admin by
    value -- unlike the outer function reference itself, an inner call
    within the same module isn't a separately bound alias.
    """
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        sole_admin = _make_staff(db, group.group_id, "admin")
        staff_ids = [sole_admin.staff_id]

        import core.admin_invariants as admin_invariants

        real_count = admin_invariants.count_active_admins

        def _scoped_count(db_, group_id):
            if group_id == group.group_id:
                return 1  # exactly this test's one admin, ignoring any real admins in the shared DB
            return real_count(db_, group_id)

        monkeypatch.setattr(admin_invariants, "count_active_admins", _scoped_count)

        resp = client.patch(
            f"/admin/kefu-staff/{sole_admin.staff_id}", json={"role": "accountant"}, headers=AUTH
        )
        assert resp.status_code == 409

        resp = client.patch(
            f"/admin/kefu-staff/{sole_admin.staff_id}", json={"is_active": False}, headers=AUTH
        )
        assert resp.status_code == 409

        db.refresh(sole_admin)
        role = db.query(Role).filter_by(role_id=sole_admin.role_id).one()
        assert role.name == "admin"
        assert sole_admin.is_active is True
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_promoting_someone_new_to_admin_never_trips_the_invariant(client):
    db = SessionLocal()
    staff_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = _make_staff(db, group.group_id, "pending")
        staff_ids = [staff.staff_id]

        resp = client.patch(f"/admin/kefu-staff/{staff.staff_id}", json={"role": "admin"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "admin"
    finally:
        _cleanup(db, staff_ids)
        db.close()


def test_unknown_staff_id_returns_404(client):
    resp = client.patch(f"/admin/kefu-staff/{uuid.uuid4()}", json={"is_active": False}, headers=AUTH)
    assert resp.status_code == 404
