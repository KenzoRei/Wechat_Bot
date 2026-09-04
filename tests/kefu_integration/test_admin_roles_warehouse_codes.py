"""GET /admin/roles now also returns the server's real VALID_WAREHOUSE_CODES
list as a top-level key, so the admin panel's warehouse checkboxes can
never silently drift from what the backend actually accepts."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from api.admin import roles as roles_module
from database import SessionLocal, get_db
from core.uchoice_constants import VALID_WAREHOUSE_CODES


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


def test_roles_response_includes_warehouse_codes_top_level_key():
    resp = _client().get("/admin/roles", headers={"X-Admin-Key": config.ADMIN_API_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["warehouse_codes"] == sorted(VALID_WAREHOUSE_CODES)
