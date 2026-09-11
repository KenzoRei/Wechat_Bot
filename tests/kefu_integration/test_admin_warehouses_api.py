"""
Real Postgres coverage for the /admin/warehouses admin surface: CRUD on the
company's own shipping-origin directory (distinct from U-Choice's own
JFK/DE/NJ inventory warehouse_code concept).
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import warehouses as warehouses_module
from database import SessionLocal, get_db


def _client():
    app = FastAPI()
    app.include_router(warehouses_module.router)

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


def _fresh_abbr() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


def _cleanup(abbr: str):
    db = SessionLocal()
    try:
        db.execute(text("delete from company_warehouse where warehouse_abbr = :a"), {"a": abbr})
        db.commit()
    finally:
        db.close()


def test_create_get_update_warehouse():
    client = _client()
    abbr = _fresh_abbr()
    try:
        create_resp = client.post("/admin/warehouses", headers=_headers(), json={
            "warehouse_abbr": abbr, "company_name": "Test Co", "addr": "1 Test St", "city": "Testville",
            "state": "TS", "zip_code": "00000", "created_by": "test_admin",
        })
        assert create_resp.status_code == 201, create_resp.text
        assert create_resp.json()["data"]["warehouse_abbr"] == abbr

        get_resp = client.get(f"/admin/warehouses/{abbr}", headers=_headers())
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["city"] == "Testville"

        update_resp = client.patch(f"/admin/warehouses/{abbr}", headers=_headers(), json={
            "contact": "New Contact", "updated_by": "test_admin",
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["contact"] == "New Contact"
        assert update_resp.json()["data"]["city"] == "Testville"  # untouched field survives
    finally:
        _cleanup(abbr)


def test_create_duplicate_warehouse_abbr_conflicts():
    client = _client()
    abbr = _fresh_abbr()
    try:
        body = {
            "warehouse_abbr": abbr, "company_name": "Test Co", "addr": "1 Test St", "city": "Testville",
            "state": "TS", "zip_code": "00000", "created_by": "test_admin",
        }
        assert client.post("/admin/warehouses", headers=_headers(), json=body).status_code == 201
        dup_resp = client.post("/admin/warehouses", headers=_headers(), json=body)
        assert dup_resp.status_code == 409
    finally:
        _cleanup(abbr)


def test_get_unknown_warehouse_404s():
    client = _client()
    resp = client.get("/admin/warehouses/ZZZZZZ", headers=_headers())
    assert resp.status_code == 404


def test_update_unknown_warehouse_404s():
    client = _client()
    resp = client.patch("/admin/warehouses/ZZZZZZ", headers=_headers(), json={"updated_by": "test_admin"})
    assert resp.status_code == 404


def test_list_warehouses_includes_seeded_real_data():
    client = _client()
    resp = client.get("/admin/warehouses", headers=_headers())
    assert resp.status_code == 200
    abbrs = {w["warehouse_abbr"] for w in resp.json()["data"]}
    assert {"JFK", "DE", "LAX", "ORD", "NJ"} <= abbrs


def test_endpoints_require_admin_key():
    client = _client()
    resp = client.get("/admin/warehouses")
    assert resp.status_code in (401, 422)  # 422 if header entirely absent per FastAPI's Header(...) validation
