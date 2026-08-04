"""
compute_invoice() — U-Choice's aggregate warehouse operating cost report for a
given month. Warehouse-level, not per-customer — U-Choice owns its own
inventory, there's no "whose pallets" question to answer. Uses completed_at,
not created_at: bill for service actually rendered, not just requested.
Shared by the on-demand view_invoice handler and the monthly scheduled push.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session as DBSession
from models.request_log import RequestLog
from models.service import ServiceType
from models.uchoice import UchoiceStorageFeeLedger


def compute_invoice(db: DBSession, warehouse_code: str, target_month: str) -> dict:
    """target_month: 'YYYY-MM'."""
    year, month = (int(p) for p in target_month.split("-"))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    end_exclusive = end + timedelta(days=1)

    transportation_total = Decimal("0")
    palletization_total = Decimal("0")
    unpacking_total = Decimal("0")

    outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").first()
    if outbound_type:
        for log in db.query(RequestLog).filter(
            RequestLog.service_type_id == outbound_type.service_type_id,
            RequestLog.status == "success",
            RequestLog.completed_at >= start,
            RequestLog.completed_at < end_exclusive,
        ).all():
            result = log.result or {}
            if result.get("warehouse_code") != warehouse_code:
                continue
            transportation_total += Decimal(str(result.get("transportation_fee", 0)))
            palletization_total += Decimal(str(result.get("palletization_fee", 0)))

    inbound_type = db.query(ServiceType).filter_by(name="uchoice_inbound_request").first()
    if inbound_type:
        for log in db.query(RequestLog).filter(
            RequestLog.service_type_id == inbound_type.service_type_id,
            RequestLog.status == "success",
            RequestLog.completed_at >= start,
            RequestLog.completed_at < end_exclusive,
        ).all():
            result = log.result or {}
            if result.get("warehouse_code") != warehouse_code:
                continue
            unpacking_total += Decimal(str(result.get("unpacking_fee", 0)))

    ledger_rows = db.query(UchoiceStorageFeeLedger).filter(
        UchoiceStorageFeeLedger.warehouse_code == warehouse_code,
        UchoiceStorageFeeLedger.fee_date >= start,
        UchoiceStorageFeeLedger.fee_date <= end,
    ).all()
    storage_fee_total = sum((row.storage_fee for row in ledger_rows), Decimal("0"))

    total = transportation_total + palletization_total + unpacking_total + storage_fee_total

    return {
        "warehouse_code":     warehouse_code,
        "target_month":       target_month,
        "transportation_fee": transportation_total,
        "palletization_fee":  palletization_total,
        "unpacking_fee":      unpacking_total,
        "storage_fee":        storage_fee_total,
        "total":              total,
    }
