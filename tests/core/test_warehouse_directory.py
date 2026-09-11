"""
Real-Postgres coverage for core/warehouse_directory.py (the company's own
physical warehouse/shipping-origin directory -- distinct from
core.uchoice_constants.VALID_WAREHOUSE_CODES, U-Choice's own inventory
warehouse_code concept) and its wiring into AI context via
core/session_manager.py's _build_uchoice_candidates.
"""
import uuid
from uuid import UUID

from database import SessionLocal
from core import warehouse_directory
from core import session_manager
from core.access_control import AccessResult


def _fresh_abbr() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


def _cleanup(db, abbr):
    from sqlalchemy import text
    db.execute(text("delete from company_warehouse where warehouse_abbr = :a"), {"a": abbr})
    db.commit()


def test_v29_seed_data_present():
    """The 5 real warehouses provided by the business must be seeded and
    queryable -- this is production reference data, not test fixture data,
    so this asserts against the real, known values rather than creating
    its own rows."""
    db = SessionLocal()
    try:
        jfk = warehouse_directory.get_warehouse(db, "JFK")
        assert jfk is not None
        assert jfk.city == "Jamaica"
        assert jfk.state == "NY"
        assert jfk.company_name == "TWF-JFK"

        lax = warehouse_directory.get_warehouse(db, "LAX")
        assert lax is not None
        assert lax.email == "lax@transworldus.com"
        assert lax.company_name == "TWF-LAX"

        nj = warehouse_directory.get_warehouse(db, "NJ")
        assert nj is not None
        assert nj.company_name == "TWW"  # a genuinely different entity from the TWF-* warehouses

        all_warehouses = warehouse_directory.list_warehouses(db)
        abbrs = {w.warehouse_abbr for w in all_warehouses}
        assert {"JFK", "DE", "LAX", "ORD", "NJ"} <= abbrs
    finally:
        db.close()


def test_get_warehouse_returns_none_for_unknown_abbr():
    db = SessionLocal()
    try:
        assert warehouse_directory.get_warehouse(db, "ZZZZZZ") is None
    finally:
        db.close()


def test_upsert_warehouse_creates_then_updates():
    db = SessionLocal()
    abbr = _fresh_abbr()
    try:
        created = warehouse_directory.upsert_warehouse(
            db, abbr, actor="test",
            company_name="Test Co", addr="1 Test St", city="Testville", state="TS", zip_code="00000",
            contact="Tester", phone="1234567890",
        )
        assert created.warehouse_abbr == abbr
        assert created.city == "Testville"

        updated = warehouse_directory.upsert_warehouse(db, abbr, actor="test2", city="NewCity")
        assert updated.city == "NewCity"
        assert updated.addr == "1 Test St"  # untouched fields survive a partial update
    finally:
        _cleanup(db, abbr)
        db.close()


def test_warehouse_candidates_shape_is_json_serializable_plain_dicts():
    db = SessionLocal()
    try:
        candidates = warehouse_directory.warehouse_candidates(db)
        assert len(candidates) >= 5
        jfk = next(c for c in candidates if c["warehouse_abbr"] == "JFK")
        assert jfk["contact"] == "Jeff"
        assert jfk["city"] == "Jamaica"
        assert jfk["company_name"] == "TWF-JFK"
        assert "email" not in jfk  # not needed for filling shipper_* fields
    finally:
        db.close()


def _fake_access(allowed_services) -> AccessResult:
    return AccessResult(
        wechat_openid="test-openid", group_id=UUID(int=0), role="admin", role_id=UUID(int=0),
        display_name="Test Admin", warehouse_codes=None, billing_customer_id=None,
        allowed_services=allowed_services, group_context=None, group_description=None,
    )


def test_build_candidates_includes_company_warehouses_for_fedex_label():
    db = SessionLocal()
    try:
        access = _fake_access([{"name": "fedex_label", "service_type_id": "dummy"}])
        candidates = session_manager._build_uchoice_candidates(db, access, None, "做一个fedex面单")
        assert "company_warehouses" in candidates
        abbrs = {w["warehouse_abbr"] for w in candidates["company_warehouses"]}
        assert "JFK" in abbrs
    finally:
        db.close()


def test_build_candidates_includes_company_warehouses_for_ups_label():
    db = SessionLocal()
    try:
        access = _fake_access([{"name": "ups_label", "service_type_id": "dummy"}])
        candidates = session_manager._build_uchoice_candidates(db, access, None, "")
        assert "company_warehouses" in candidates
    finally:
        db.close()


def test_build_candidates_omits_company_warehouses_for_unrelated_service():
    db = SessionLocal()
    try:
        access = _fake_access([{"name": "view_storage", "service_type_id": "dummy"}])
        candidates = session_manager._build_uchoice_candidates(db, access, None, "")
        assert "company_warehouses" not in candidates
    finally:
        db.close()
