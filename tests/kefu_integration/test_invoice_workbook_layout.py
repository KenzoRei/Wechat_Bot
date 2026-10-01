"""
Invoice workbook layout (invoice-inventory plan, Phase 1): every detail
sheet starts with a Warehouse column, the detail sheets are named
Outbound/Inbound/Storage, and each has a frozen header row and a filter.

Real Postgres DB, read-only: builds a workbook from whatever rows exist and
checks structure only, never row counts.
"""
import datetime
import io

import pytest
from openpyxl import load_workbook

from database import SessionLocal

FIXED = datetime.datetime(2026, 9, 30, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def ledger_row(db):
    """One JFK storage-fee row in the period, so the row check isn't vacuous.
    Removed by exact id afterwards; skips the insert if the date is taken."""
    from models.uchoice import UchoiceStorageFeeLedger

    fee_date = datetime.date(2026, 9, 15)
    existing = db.query(UchoiceStorageFeeLedger).filter_by(warehouse_code="JFK", fee_date=fee_date).first()
    created = None
    if existing is None:
        created = UchoiceStorageFeeLedger(warehouse_code="JFK", fee_date=fee_date, pallet_count=3, storage_fee=1.5)
        db.add(created)
        db.commit()
    yield
    if created is not None:
        db.rollback()
        db.query(UchoiceStorageFeeLedger).filter_by(ledger_id=created.ledger_id).delete()
        db.commit()


@pytest.fixture
def workbook(db, ledger_row):
    from core.uchoice_invoice_export import build_invoice_workbook

    data = build_invoice_workbook(db, "JFK", "2026-09", generated_at=FIXED)
    return load_workbook(io.BytesIO(data))


def test_sheet_names(workbook):
    assert workbook.sheetnames == ["Summary", "Outbound", "Inbound", "Storage"]


@pytest.mark.parametrize("sheet, headers", [
    ("Outbound", ["Warehouse", "Serial Number", "Completed At (UTC)", "SKU Lines",
                  "Destination Company", "Destination Address",
                  "Transportation Fee", "Palletization Fee"]),
    ("Inbound", ["Warehouse", "Serial Number", "Completed At (UTC)", "SKU Lines", "Unpacking Fee"]),
    ("Storage", ["Warehouse", "Date", "Pallet Count", "Storage Fee"]),
])
def test_detail_sheet_headers_start_with_warehouse(workbook, sheet, headers):
    ws = workbook[sheet]
    assert [c.value for c in ws[1]] == headers


@pytest.mark.parametrize("sheet", ["Outbound", "Inbound", "Storage"])
def test_detail_rows_carry_their_warehouse(workbook, sheet):
    rows = list(workbook[sheet].iter_rows(min_row=2, values_only=True))
    if sheet == "Storage":
        assert rows, "seeded ledger row missing"
    assert all(row[0] == "JFK" for row in rows)


@pytest.mark.parametrize("sheet", ["Outbound", "Inbound", "Storage"])
def test_detail_sheets_have_frozen_header_and_filter(workbook, sheet):
    ws = workbook[sheet]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref and ws.auto_filter.ref.startswith("A1:")
