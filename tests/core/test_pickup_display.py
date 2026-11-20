"""
Customer pickup wording across the renderers (plan: shared pickup display fact,
D5, D14). The wording is built from the request's OWN fields in core/pickup.py
and every builder calls it; an ordinary delivery renders exactly as before.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import confirmation, kefu_turn_apply, pickup
from core.uchoice_context import _display_fields
from models.uchoice import UchoiceAddress

GENERAL = uuid.UUID("a1000000-0042-0000-0000-000000000001")
JFK_RET = uuid.UUID("bb8c35cb-dda1-404d-9294-682ed3df5659")
PLAIN = uuid.UUID("c0ffee00-1111-4111-8111-111111111111")

CUSTOMER = {"pickup_type": "customer", "pickup_customer_name": "王小明", "warehouse_code": "JFK",
            "destination_address_id": GENERAL, "sku_lines": []}
RETENTION = {"pickup_type": "retention", "warehouse_code": "JFK", "destination_address_id": JFK_RET, "sku_lines": []}
DELIVERY = {"warehouse_code": "JFK", "destination_address_id": PLAIN, "sku_lines": []}


def _row(address_id, company, charge, addr, wh, kind):
    return UchoiceAddress(address_id=address_id, company_name=company, charge_type=charge, addr=addr,
                          warehouse_code=wh, created_by="test", pickup_kind=kind,
                          created_at=datetime.now(timezone.utc))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    UchoiceAddress.__table__.create(engine)
    sql = sessionmaker(bind=engine)()
    sql.add_all([
        _row(JFK_RET, "JFK仓库自提留存", "self_pickup", "14502 156th St", "JFK", "retention"),
        _row(GENERAL, "客户自提", "self_pickup", "", None, "customer"),
        _row(PLAIN, "ABC Trading", "delivery", "123 Main St", "JFK", None),
    ])
    sql.commit()
    yield sql
    sql.close()


@pytest.fixture(autouse=True)
def _no_sku_table(monkeypatch):
    monkeypatch.setattr(confirmation, "_sku_label_map", lambda db: {})


def _items(sections):
    return [i for s in sections for i in s["items"]]


# ── request confirmation (requester sees what will print) ─────────────────────

def test_request_confirmation_shows_the_pickup_type_and_name(db):
    items = _items(confirmation.build_confirmation_sections("uchoice_outbound_request", CUSTOMER, db))
    assert "目的地：客户自提 — 取货人：王小明" in items


def test_request_confirmation_for_retention(db):
    items = _items(confirmation.build_confirmation_sections("uchoice_outbound_request", RETENTION, db))
    assert "目的地：仓库自提留存" in items


def test_request_confirmation_for_an_ordinary_delivery_is_unchanged(db):
    items = _items(confirmation.build_confirmation_sections("uchoice_outbound_request", DELIVERY, db))
    assert "目的地：ABC Trading（123 Main St）" in items


def test_a_grandfathered_request_with_no_pickup_type_still_renders_its_row_label(db):
    old = {"warehouse_code": "JFK", "destination_address_id": JFK_RET, "sku_lines": []}
    items = _items(confirmation.build_confirmation_sections("uchoice_outbound_request", old, db))
    assert "目的地：JFK仓库自提留存（14502 156th St）" in items


# ── warehouse completion confirmation (the one extra line) ────────────────────

def test_single_completion_adds_exactly_one_pickup_warning_line(db):
    items = confirmation._completion_destination_items(db, CUSTOMER, with_pickup_warning=True)
    assert items == ["目的地：客户自提 — 取货人：王小明", "⚠️ 自提：无需送货，货物由客户到仓提取；如非自提请先联系客服"]


def test_batch_completion_stays_one_line_per_request(db):
    assert confirmation._completion_destination_items(db, CUSTOMER) == ["目的地：客户自提 — 取货人：王小明"]


def test_retention_completion_warning(db):
    items = confirmation._completion_destination_items(db, RETENTION, with_pickup_warning=True)
    assert items[0] == "目的地：仓库自提留存" and items[1].startswith("⚠️ 仓库自提留存：货物留在仓库，无需送货")


def test_completion_of_an_ordinary_delivery_has_no_warning(db):
    items = confirmation._completion_destination_items(db, DELIVERY, with_pickup_warning=True)
    assert items == ["目的地：ABC Trading（123 Main St）"]


# ── invoice detail sheet (D14) ────────────────────────────────────────────────

def test_invoice_destination_cells_for_a_customer_pickup(db):
    from core.uchoice_invoice_export import _destination_cells
    addr = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == GENERAL).one()
    assert _destination_cells(CUSTOMER, addr) == ("客户自提（王小明）", "")


def test_invoice_destination_cells_are_unchanged_for_everything_else(db):
    from core.uchoice_invoice_export import _destination_cells
    plain = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == PLAIN).one()
    ret = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == JFK_RET).one()
    assert _destination_cells(DELIVERY, plain) == ("ABC Trading", "123 Main St")
    assert _destination_cells(RETENTION, ret) == ("JFK仓库自提留存", "14502 156th St")
    old = {"warehouse_code": "JFK", "destination_address_id": JFK_RET}
    assert _destination_cells(old, ret) == ("JFK仓库自提留存", "14502 156th St")


# ── pending list `→` line ─────────────────────────────────────────────────────

def _row_log():
    return SimpleNamespace(log_id="log-1")


def test_pending_list_arrow_uses_the_pickup_label_and_no_address_suffix(db):
    addr = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == GENERAL).one()
    shown = _display_fields(_row_log(), CUSTOMER, addr, {}, {})
    assert shown["destination_name"] == "【自提】客户自提：王小明（无需送货）"
    assert "destination_address" not in shown


def test_two_requests_for_the_same_pickup_name_get_no_address_suffix(db):
    addr = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == GENERAL).one()
    cands = []
    for serial in ("REQ-A", "REQ-B"):
        c = {"serial_number": serial, "warehouse_code": "JFK", "created_at": "2026-11-20T10:00:00+00:00"}
        c.update(_display_fields(_row_log(), CUSTOMER, addr, {}, {}))
        cands.append(c)
    options = kefu_turn_apply._pending_candidate_options(cands, 10)
    assert all("→ 【自提】客户自提：王小明（无需送货）" in o.details for o in options)


def test_pending_list_arrow_for_retention_and_ordinary_delivery(db):
    ret = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == JFK_RET).one()
    plain = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == PLAIN).one()
    assert _display_fields(_row_log(), RETENTION, ret, {}, {})["destination_name"] == "【自提】仓库自提留存（货不出仓）"
    assert _display_fields(_row_log(), DELIVERY, plain, {}, {})["destination_name"] == "ABC Trading"


def test_an_old_request_without_pickup_type_keeps_its_row_label_in_the_list(db):
    ret = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == JFK_RET).one()
    old = {"warehouse_code": "JFK", "destination_address_id": JFK_RET, "sku_lines": []}
    assert _display_fields(_row_log(), old, ret, {}, {})["destination_name"] == "JFK仓库自提留存"


def test_a_long_pickup_name_is_truncated_in_lists_only(db):
    long_fields = {**CUSTOMER, "pickup_customer_name": "王" * 40}
    label = pickup.list_label(long_fields)
    assert label.count("王") <= pickup.LIST_NAME_MAX_LEN and "…" in label
    assert pickup.confirmation_destination(long_fields).count("王") <= pickup.LIST_NAME_MAX_LEN
    assert pickup.invoice_destination(long_fields).count("王") == 40       # the billing record keeps the full name
