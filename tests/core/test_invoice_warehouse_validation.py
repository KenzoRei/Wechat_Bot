"""
view_invoice warehouse_code validation (invoice-inventory plan, Phase 1).

An unknown code used to pass for unscoped roles (admin/accountant) and
produce an all-zero invoice, because the invoice queries filter by exact
equality. Both the chat pre-confirm validator and the admin export endpoints
must now reject it. Offline: neither path touches the database before
rejecting (db=None / a get_db override that yields None).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from core import pre_confirm_validators
from core.role_policy import OUT_OF_WAREHOUSE_SCOPE_MESSAGE
from database import get_db

ADMIN = {"role": "admin", "warehouse_codes": None}


@pytest.mark.parametrize("bad_code", ["JFK,DE", "all", "jfk", "LAX", " JFK"])
def test_unknown_code_rejected_for_unscoped_role(bad_code):
    error = pre_confirm_validators.run("view_invoice", ADMIN, {"warehouse_code": bad_code}, None)
    assert error is not None
    assert bad_code in error
    for code in ("DE", "JFK", "NJ"):
        assert code in error


@pytest.mark.parametrize("code", ["JFK", "DE", "NJ"])
def test_known_code_accepted_for_unscoped_role(code):
    assert pre_confirm_validators.run("view_invoice", ADMIN, {"warehouse_code": code}, None) is None


def test_missing_code_left_to_required_field_check():
    assert pre_confirm_validators.run("view_invoice", ADMIN, {}, None) is None
    assert pre_confirm_validators.run("view_invoice", ADMIN, {"warehouse_code": ""}, None) is None


def test_scope_check_still_applies_after_known_code_check():
    warehouseman = {"role": "warehouseman", "warehouse_codes": ["JFK"]}
    assert pre_confirm_validators.run("view_invoice", warehouseman, {"warehouse_code": "JFK"}, None) is None
    assert pre_confirm_validators.run(
        "view_invoice", warehouseman, {"warehouse_code": "DE"}, None,
    ) == OUT_OF_WAREHOUSE_SCOPE_MESSAGE


@pytest.fixture
def client():
    from api.admin import invoices as invoices_module

    app = FastAPI()
    app.include_router(invoices_module.router)

    def _no_db():
        yield None  # the warehouse check must reject before any query runs

    app.dependency_overrides[get_db] = _no_db
    return TestClient(app)


AUTH = {"X-Admin-Key": config.ADMIN_API_KEY}


@pytest.mark.parametrize("path", ["/admin/invoices/export", "/admin/invoices/export-link"])
@pytest.mark.parametrize("bad_code", ["all", "JFK,DE", "jfk"])
def test_admin_export_rejects_unknown_warehouse(client, path, bad_code):
    response = client.get(path, params={"warehouse_code": bad_code, "start_month": "2026-09"}, headers=AUTH)
    assert response.status_code == 400
    assert "DE, JFK, NJ" in response.json()["detail"]
