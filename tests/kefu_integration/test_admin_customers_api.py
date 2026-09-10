"""
Real Postgres coverage for the new /admin/customers admin surface: CRUD on
the customer master record, and the write-only credential endpoints (never
echo a value back, list-status endpoint never exposes plaintext/ciphertext).
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import customers as customers_module
from database import SessionLocal, get_db


def _client():
    app = FastAPI()
    app.include_router(customers_module.router)

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


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _cleanup(customer_id: str):
    db = SessionLocal()
    try:
        db.execute(text("delete from customer_credential where customer_id = :cid"), {"cid": customer_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
    finally:
        db.close()


def test_create_get_update_customer():
    client = _client()
    customer_id = _fresh_customer_id()
    try:
        create_resp = client.post("/admin/customers", headers=_headers(), json={
            "customer_id": customer_id, "display_name": "Test Co", "created_by": "test_admin",
        })
        assert create_resp.status_code == 201, create_resp.text
        assert create_resp.json()["data"]["customer_id"] == customer_id

        get_resp = client.get(f"/admin/customers/{customer_id}", headers=_headers())
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["display_name"] == "Test Co"

        update_resp = client.patch(f"/admin/customers/{customer_id}", headers=_headers(), json={
            "display_name": "Test Co Updated",
            "rate_multiplier": {"fedex": 1.3},
            "updated_by": "test_admin",
        })
        assert update_resp.status_code == 200
        body = update_resp.json()["data"]
        assert body["display_name"] == "Test Co Updated"
        assert body["rate_multiplier"] == {"fedex": 1.3}
    finally:
        _cleanup(customer_id)


def test_create_rejects_duplicate_id():
    client = _client()
    customer_id = _fresh_customer_id()
    try:
        client.post("/admin/customers", headers=_headers(), json={
            "customer_id": customer_id, "display_name": "First", "created_by": "test_admin",
        })
        dup_resp = client.post("/admin/customers", headers=_headers(), json={
            "customer_id": customer_id, "display_name": "Second", "created_by": "test_admin",
        })
        assert dup_resp.status_code == 409
    finally:
        _cleanup(customer_id)


def test_create_rejects_invalid_status():
    client = _client()
    customer_id = _fresh_customer_id()
    resp = client.post("/admin/customers", headers=_headers(), json={
        "customer_id": customer_id, "display_name": "Bad Status Co",
        "status": "not_a_real_status", "created_by": "test_admin",
    })
    assert resp.status_code == 400


def test_get_unknown_customer_404():
    client = _client()
    resp = client.get("/admin/customers/F999999", headers=_headers())
    assert resp.status_code == 404


def test_missing_admin_key_rejected():
    client = _client()
    resp = client.get("/admin/customers")
    assert resp.status_code in (401, 422)  # 422 if header entirely absent per FastAPI's Header(...) validation


def test_credential_write_only_never_echoes_value():
    client = _client()
    customer_id = _fresh_customer_id()
    try:
        client.post("/admin/customers", headers=_headers(), json={
            "customer_id": customer_id, "display_name": "Credential Co", "created_by": "test_admin",
        })
        set_resp = client.post(f"/admin/customers/{customer_id}/credentials", headers=_headers(), json={
            "credential_type": "ydd_password", "value": "s3cr3t-pw", "updated_by": "test_admin",
        })
        assert set_resp.status_code == 201
        assert "s3cr3t-pw" not in set_resp.text

        status_resp = client.get(f"/admin/customers/{customer_id}/credentials", headers=_headers())
        assert status_resp.status_code == 200
        assert "s3cr3t-pw" not in status_resp.text
        data = status_resp.json()["data"]
        assert len(data) == 1
        assert data[0]["credential_type"] == "ydd_password"
        assert "value" not in data[0] and "encrypted_value" not in data[0]
    finally:
        _cleanup(customer_id)


def test_credential_rejects_unknown_type_and_empty_value():
    client = _client()
    customer_id = _fresh_customer_id()
    try:
        client.post("/admin/customers", headers=_headers(), json={
            "customer_id": customer_id, "display_name": "Reject Co", "created_by": "test_admin",
        })
        bad_type = client.post(f"/admin/customers/{customer_id}/credentials", headers=_headers(), json={
            "credential_type": "not_real", "value": "x", "updated_by": "test_admin",
        })
        assert bad_type.status_code == 400

        empty_value = client.post(f"/admin/customers/{customer_id}/credentials", headers=_headers(), json={
            "credential_type": "ydd_username", "value": "", "updated_by": "test_admin",
        })
        assert empty_value.status_code == 400
    finally:
        _cleanup(customer_id)
