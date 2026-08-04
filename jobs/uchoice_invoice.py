"""
U-Choice monthly invoice push — reuses core.uchoice_invoice.compute_invoice()
exactly, the same function the on-demand view_invoice service uses. Pushes
into every group that had U-Choice activity in the given warehouse that
month; no blank invoices for zero-activity combinations. This is U-Choice's
own aggregate warehouse operating cost, not a per-customer bill — every
group active in the same warehouse sees the identical total, by design.

Registered in main.py the same way as jobs/session_expiry.py, on a monthly
cron trigger.
"""
from datetime import date, timedelta
from sqlalchemy.orm import Session as DBSession

from models.request_log import RequestLog
from models.service import ServiceType
from models.group import GroupConfig
from core.uchoice_invoice import compute_invoice
from clients.wechat_client import send_group_webhook_message


def run_uchoice_invoice(db: DBSession, target_month: str | None = None) -> None:
    """target_month: 'YYYY-MM'. Defaults to the month that just ended."""
    target_month = target_month or _previous_month_str()

    service_type_ids = [
        st.service_type_id for st in
        db.query(ServiceType).filter(
            ServiceType.name.in_(["uchoice_inbound_request", "uchoice_outbound_request"])
        ).all()
    ]
    if not service_type_ids:
        return

    year, month = (int(p) for p in target_month.split("-"))
    start = date(year, month, 1)
    end_exclusive = _next_month_date(year, month)

    rows = (
        db.query(RequestLog)
        .filter(
            RequestLog.service_type_id.in_(service_type_ids),
            RequestLog.status == "success",
            RequestLog.completed_at >= start,
            RequestLog.completed_at < end_exclusive,
        )
        .all()
    )

    combos = set()
    for r in rows:
        warehouse_code = (r.result or {}).get("warehouse_code")
        if warehouse_code and r.group_id:
            combos.add((warehouse_code, r.group_id))

    invoice_cache: dict = {}
    for warehouse_code, group_id in combos:
        if warehouse_code not in invoice_cache:
            invoice_cache[warehouse_code] = compute_invoice(db, warehouse_code, target_month)
        invoice = invoice_cache[warehouse_code]

        group = db.query(GroupConfig).filter_by(group_id=group_id).first()
        webhook_url = group.group_robot_webhook_url if group else None
        if not webhook_url:
            continue

        content = (
            f"🧾 U-Choice {warehouse_code} {target_month} 月度费用报告\n"
            f"运输费：${invoice['transportation_fee']}\n"
            f"打托费：${invoice['palletization_fee']}\n"
            f"拆包费：${invoice['unpacking_fee']}\n"
            f"仓储费：${invoice['storage_fee']}\n"
            f"合计：${invoice['total']}"
        )
        try:
            send_group_webhook_message(webhook_url, content)
        except RuntimeError as e:
            print(f"[uchoice_invoice] push failed for group {group_id}: {e}", flush=True)


def _previous_month_str() -> str:
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    return f"{last_month_end.year:04d}-{last_month_end.month:02d}"


def _next_month_date(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)
