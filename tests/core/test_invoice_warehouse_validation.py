"""
费用报告 (view_invoice) warehouse rules (invoice-inventory plan, Phases 1 + 3).

- The warehouse is optional; omitted (or "all") means every warehouse the
  caller may see, resolved once when the request runs.
- Every resolved code must be a real warehouse (an unknown one used to give
  an all-zero invoice) and within the caller's scope.
- A Kefu case re-checks its frozen list on later turns, never re-expands it.
- The admin export accepts one code, a comma list, or "all".

Offline: none of these paths touch the database (db=None / a get_db
override that yields None).
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from core import pre_confirm_validators
from core.role_policy import MISSING_WAREHOUSE_SCOPE_MESSAGE, OUT_OF_WAREHOUSE_SCOPE_MESSAGE
from core.uchoice_invoice_scope import (
    invoice_warehouses_for_execution, requested_invoice_warehouses, resolve_invoice_warehouses,
)
from database import get_db

ADMIN = {"role": "admin", "warehouse_codes": None}
ACCOUNTANT = {"role": "accountant", "warehouse_codes": None}
ALL = ["DE", "JFK", "NJ"]


# ── Resolution: omitted = every accessible warehouse ─────────────────────────

@pytest.mark.parametrize("context, expected", [
    (ADMIN, ALL),
    (ACCOUNTANT, ALL),
    ({"role": "warehouseman", "warehouse_codes": ["JFK"]}, ["JFK"]),
    ({"role": "warehouseman", "warehouse_codes": ["NJ", "JFK"]}, ["JFK", "NJ"]),
])
def test_omitted_warehouse_resolves_to_all_accessible(context, expected):
    fields, denial = resolve_invoice_warehouses(context, {"start_month": "2026-09"})
    assert denial is None
    assert fields["warehouse_codes"] == expected
    assert fields["start_month"] == "2026-09"


def test_scoped_role_without_assignment_is_denied_not_given_all():
    fields, denial = resolve_invoice_warehouses({"role": "warehouseman", "warehouse_codes": []}, {})
    assert denial == MISSING_WAREHOUSE_SCOPE_MESSAGE
    assert "warehouse_codes" not in fields
    with pytest.raises(RuntimeError):
        invoice_warehouses_for_execution({"role": "warehouseman", "warehouse_codes": None}, {})


@pytest.mark.parametrize("fields, expected", [
    ({"warehouse_codes": ["JFK", "DE", "JFK"]}, ["DE", "JFK"]),
    ({"warehouse_codes": "JFK,DE"}, ["DE", "JFK"]),
    ({"warehouse_codes": "JFK、NJ"}, ["JFK", "NJ"]),
    ({"warehouse_code": "NJ"}, ["NJ"]),                       # legacy single field
    ({"warehouse_codes": ["DE"], "warehouse_code": "JFK"}, ["DE"]),
    ({"warehouse_codes": ["全部"]}, []),
    ({"warehouse_codes": ["all"]}, []),
    ({"warehouse_code": "ALL"}, []),
    ({}, []),
])
def test_requested_warehouses(fields, expected):
    assert requested_invoice_warehouses(fields) == expected


def test_all_token_resolves_like_omitted():
    fields, _ = resolve_invoice_warehouses({"role": "warehouseman", "warehouse_codes": ["DE"]},
                                           {"warehouse_codes": ["全部"]})
    assert fields["warehouse_codes"] == ["DE"]


def test_explicit_list_is_kept_and_resolution_is_idempotent():
    once, _ = resolve_invoice_warehouses(ADMIN, {"warehouse_codes": ["JFK"]})
    twice, _ = resolve_invoice_warehouses(ADMIN, once)
    assert once["warehouse_codes"] == twice["warehouse_codes"] == ["JFK"]


def test_legacy_warehouse_code_session_still_executes():
    assert invoice_warehouses_for_execution(ADMIN, {"warehouse_code": "JFK"}) == ["JFK"]


# ── Validation: every code real and in scope ───────────────────────────────

@pytest.mark.parametrize("fields, bad", [
    ({"warehouse_codes": ["JFK", "LAX"]}, "LAX"),
    ({"warehouse_codes": ["jfk"]}, "jfk"),
    ({"warehouse_code": "JFK,DE"}, "JFK,DE"),                 # legacy field is one code, not a list
])
def test_unknown_code_rejected_for_unscoped_role(fields, bad):
    error = pre_confirm_validators.run("view_invoice", ADMIN, fields, None)
    assert error is not None and bad in error
    for code in ALL:
        assert code in error


@pytest.mark.parametrize("fields", [{"warehouse_codes": ALL}, {"warehouse_codes": ["NJ"]}, {"warehouse_code": "DE"}])
def test_known_codes_accepted_for_unscoped_role(fields):
    assert pre_confirm_validators.run("view_invoice", ADMIN, fields, None) is None


def test_scope_checked_for_every_code():
    warehouseman = {"role": "warehouseman", "warehouse_codes": ["JFK"]}
    assert pre_confirm_validators.run("view_invoice", warehouseman, {"warehouse_codes": ["JFK"]}, None) is None
    error = pre_confirm_validators.run("view_invoice", warehouseman, {"warehouse_codes": ["JFK", "DE"]}, None)
    assert error == f"DE：{OUT_OF_WAREHOUSE_SCOPE_MESSAGE}"


# ── Kefu case re-check: frozen list, every code ─────────────────────────────

def _access(codes):
    return SimpleNamespace(group_id="g1", warehouse_codes=codes, allowed_services=[
        {"service_type_id": "svc-invoice", "name": "view_invoice"},
        {"service_type_id": "svc-role", "name": "role_change"},
    ])


def _case(service_type_id, fields):
    return SimpleNamespace(group_id="g1", service_type_id=service_type_id, collected_fields=fields)


def test_case_denied_when_any_frozen_warehouse_was_revoked():
    from core.kefu_case_adapter import _authorize_case
    case = _case("svc-invoice", {"warehouse_codes": ["DE", "JFK"]})
    assert _authorize_case(_access(["DE", "JFK"]), case) is None
    assert _authorize_case(_access(["DE"]), case) == "case_wrong_warehouse"


def test_newly_granted_warehouse_never_joins_a_frozen_case():
    from core.kefu_case_adapter import _authorize_case
    fields = {"warehouse_codes": ["JFK"]}
    assert _authorize_case(_access(["DE", "JFK"]), _case("svc-invoice", fields)) is None
    assert fields == {"warehouse_codes": ["JFK"]}


def test_unresolved_all_case_names_nothing_to_check():
    from core.kefu_case_adapter import _authorize_case
    assert _authorize_case(_access(["DE"]), _case("svc-invoice", {"warehouse_codes": ["全部"]})) is None
    assert _authorize_case(_access(["DE"]), _case("svc-invoice", {"start_month": "2026-09"})) is None


def test_role_change_assignment_list_is_not_a_case_warehouse():
    from core.kefu_case_adapter import _authorize_case
    case = _case("svc-role", {"warehouse_codes": ["NJ"], "new_role": "warehouseman"})
    assert _authorize_case(_access(["DE"]), case) is None


# ── Admin export ─────────────────────────────────────────────────────────────

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
@pytest.mark.parametrize("bad_code", ["LAX", "jfk", "JFK,LAX", ",", "all,JFK"])
def test_admin_export_rejects_unknown_warehouse(client, path, bad_code):
    response = client.get(path, params={"warehouse_code": bad_code, "start_month": "2026-09"}, headers=AUTH)
    assert response.status_code == 400
    assert "DE, JFK, NJ" in response.json()["detail"]


def test_admin_export_parses_lists_and_all():
    from api.admin.invoices import _parse_warehouses
    assert _parse_warehouses("JFK") == ["JFK"]
    assert _parse_warehouses("NJ, JFK,NJ") == ["JFK", "NJ"]
    assert _parse_warehouses("all") == ALL
    assert _parse_warehouses("ALL") == ALL
