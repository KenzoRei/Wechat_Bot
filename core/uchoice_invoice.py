"""
compute_invoice() — U-Choice's aggregate warehouse operating cost report for a
given month range. Warehouse-level, not per-customer — U-Choice owns its own
inventory, there's no "whose pallets" question to answer. Uses completed_at,
not created_at: bill for service actually rendered, not just requested.
Shared by the on-demand view_invoice handler and the monthly scheduled push.

The row-selection logic (_resolve_range, _outbound_logs, _inbound_logs,
_ledger_rows) is factored out and reused by core/uchoice_invoice_export.py's
detail workbook — both the chat summary and the Excel export read the exact
same rows, so they can never silently drift apart.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session as DBSession
from models.request_log import RequestLog
from models.service import ServiceType
from models.uchoice import UchoiceStorageFeeLedger


def _resolve_range(start_month: str, end_month: str | None) -> tuple[date, date, date]:
    """Returns (start, end, end_exclusive). end_month defaults to start_month."""
    end_month = end_month or start_month
    start_year, start_mo = (int(p) for p in start_month.split("-"))
    end_year, end_mo = (int(p) for p in end_month.split("-"))
    start = date(start_year, start_mo, 1)
    end = date(end_year, end_mo, calendar.monthrange(end_year, end_mo)[1])
    return start, end, end + timedelta(days=1)


def _completed_logs(db: DBSession, service_type_name: str, warehouse_code: str, start: date, end_exclusive: date) -> list[RequestLog]:
    """success-status logs for one U-Choice request-creation service type, filtered to one warehouse via result['warehouse_code']."""
    service_type = db.query(ServiceType).filter_by(name=service_type_name).first()
    if not service_type:
        return []
    logs = db.query(RequestLog).filter(
        RequestLog.service_type_id == service_type.service_type_id,
        RequestLog.status == "success",
        RequestLog.completed_at >= start,
        RequestLog.completed_at < end_exclusive,
    ).order_by(RequestLog.completed_at).all()
    return [log for log in logs if (log.result or {}).get("warehouse_code") == warehouse_code]


def _ledger_rows(db: DBSession, warehouse_code: str, start: date, end: date) -> list[UchoiceStorageFeeLedger]:
    return (
        db.query(UchoiceStorageFeeLedger)
        .filter(
            UchoiceStorageFeeLedger.warehouse_code == warehouse_code,
            UchoiceStorageFeeLedger.fee_date >= start,
            UchoiceStorageFeeLedger.fee_date <= end,
        )
        .order_by(UchoiceStorageFeeLedger.fee_date)
        .all()
    )


def compute_invoice(db: DBSession, warehouse_code: str, start_month: str, end_month: str | None = None) -> dict:
    """
    start_month/end_month: 'YYYY-MM', inclusive range. end_month defaults to
    start_month for a single-month invoice (same range-not-free-date
    principle as view_storage_history).
    """
    start, end, end_exclusive = _resolve_range(start_month, end_month)
    end_month = end_month or start_month

    outbound_logs = _completed_logs(db, "uchoice_outbound_request", warehouse_code, start, end_exclusive)
    transportation_total = sum((Decimal(str((log.result or {}).get("transportation_fee", 0))) for log in outbound_logs), Decimal("0"))
    palletization_total = sum((Decimal(str((log.result or {}).get("palletization_fee", 0))) for log in outbound_logs), Decimal("0"))

    inbound_logs = _completed_logs(db, "uchoice_inbound_request", warehouse_code, start, end_exclusive)
    unpacking_total = sum((Decimal(str((log.result or {}).get("unpacking_fee", 0))) for log in inbound_logs), Decimal("0"))

    ledger_rows = _ledger_rows(db, warehouse_code, start, end)
    storage_fee_total = sum((row.storage_fee for row in ledger_rows), Decimal("0"))

    total = transportation_total + palletization_total + unpacking_total + storage_fee_total

    # Opening/closing pallet totals for the chat reply's one-line inventory
    # summary; the per-SKU detail is the workbook's Inventory sheet.
    from core.uchoice_inventory import inventory_balances
    balances = inventory_balances(db, [warehouse_code], start, end_exclusive)

    return {
        "warehouse_code":     warehouse_code,
        "start_month":        start_month,
        "end_month":          end_month,
        "transportation_fee": transportation_total,
        "palletization_fee":  palletization_total,
        "unpacking_fee":      unpacking_total,
        "storage_fee":        storage_fee_total,
        "total":              total,
        "opening_pallets":    sum(b["opening"] for b in balances),
        "closing_pallets":    sum(b["closing"] for b in balances),
    }


_FEE_KEYS = ("transportation_fee", "palletization_fee", "unpacking_fee", "storage_fee", "total")
_PALLET_KEYS = ("opening_pallets", "closing_pallets")


def invoice_warehouse_list(warehouse_codes) -> list[str]:
    """Accept one code or a list; return the de-duplicated, sorted list."""
    if isinstance(warehouse_codes, str):
        warehouse_codes = [warehouse_codes]
    return sorted(dict.fromkeys(warehouse_codes or []))


def compute_combined_invoice(db: DBSession, warehouse_codes, start_month: str, end_month: str | None = None) -> dict:
    """
    One invoice over several warehouses (invoice-inventory plan, Phase 3).
    The fee logic stays in compute_invoice, run once per warehouse; this only
    adds them up. Top-level keys mirror compute_invoice's (grand totals), plus
    warehouse_codes and per_warehouse {code: compute_invoice result}.
    """
    codes = invoice_warehouse_list(warehouse_codes)
    end_month = end_month or start_month
    per_warehouse = {code: compute_invoice(db, code, start_month, end_month) for code in codes}
    combined = {
        "warehouse_codes": codes,
        "start_month":     start_month,
        "end_month":       end_month,
        "per_warehouse":   per_warehouse,
    }
    for key in _FEE_KEYS:
        combined[key] = sum((inv[key] for inv in per_warehouse.values()), Decimal("0"))
    for key in _PALLET_KEYS:
        combined[key] = sum(inv[key] for inv in per_warehouse.values())
    return combined


def serialize_invoice(invoice: dict) -> dict:
    """Decimals -> strings (nested per_warehouse too), for request_log.result JSON."""
    def _plain(value):
        if hasattr(value, "quantize"):
            return str(value)
        if isinstance(value, dict):
            return {k: _plain(v) for k, v in value.items()}
        return value
    return _plain(invoice)
