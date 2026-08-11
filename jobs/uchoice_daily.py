"""
U-Choice daily job — one pass, three responsibilities (per design doc, not
three separate schedules):
1. Digest of all still-pending (status='processing') inbound/outbound
   requests, oldest first, with a duration annotation and a warning marker
   past the 7-day threshold.
2. Retires anything past 7 days to status='stale' in the same pass — the
   digest includes a distinct section for anything that just crossed the
   threshold.
3. Computes that day's uchoice_storage_fee_ledger row per warehouse —
   piggybacks on this job already running daily.

Registered in main.py the same way as jobs/session_expiry.py.
"""
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from collections import defaultdict
from sqlalchemy.orm import Session as DBSession

from models.request_log import RequestLog
from models.service import ServiceType
from models.uchoice import UchoiceStorage, UchoiceStorageFeeLedger
from core import request_logger
from core.uchoice_rates import STORAGE_PER_PALLET_PER_DAY
from core.uchoice_constants import VALID_WAREHOUSE_CODES, STALE_THRESHOLD_DAYS
from clients.wechat_client import send_group_webhook_message

WAREHOUSES = sorted(VALID_WAREHOUSE_CODES)


def run_uchoice_daily(db: DBSession) -> None:
    _run_digest_and_retirement(db)
    _run_storage_fee_ledger(db)


def _run_digest_and_retirement(db: DBSession) -> None:
    service_type_ids = [
        st.service_type_id for st in
        db.query(ServiceType).filter(
            ServiceType.name.in_(["uchoice_inbound_request", "uchoice_outbound_request"])
        ).all()
    ]
    if not service_type_ids:
        return

    rows = (
        db.query(RequestLog)
        .filter(
            RequestLog.status == "processing",
            RequestLog.service_type_id.in_(service_type_ids),
            # kefu-migration-plan.md Sec 7: this job stays fully live for
            # Smart Robot as long as SMART_ROBOT_ENABLED -- scoped to its
            # own channel so a Kefu-originated request never appears in a
            # digest pushed to a WeCom group Kefu never touches.
            RequestLog.source_channel == "smart_robot",
        )
        .order_by(RequestLog.created_at.asc())
        .all()
    )
    if not rows:
        return

    now = datetime.now(timezone.utc)
    threshold = timedelta(days=STALE_THRESHOLD_DAYS)

    by_group: dict = defaultdict(list)
    just_retired_by_group: dict = defaultdict(list)

    for log in rows:
        age = now - log.created_at
        days = age.days
        if age >= threshold:
            request_logger.mark_stale(db, log.log_id)
            just_retired_by_group[log.group_id].append(log)
        else:
            marker = " ⚠️" if days >= STALE_THRESHOLD_DAYS - 1 else ""
            by_group[log.group_id].append((log, days, marker))

    all_group_ids = set(by_group) | set(just_retired_by_group)
    for group_id in all_group_ids:
        webhook_url = _webhook_url_for_group(db, group_id)
        if not webhook_url:
            continue

        lines = ["📋 U-Choice 待处理申请日报"]
        pending = by_group.get(group_id, [])
        if pending:
            for log, days, marker in pending:
                lines.append(f"- {log.serial_number}（{days}天前）{marker}")
        else:
            lines.append("（无待处理申请）")

        retired = just_retired_by_group.get(group_id, [])
        if retired:
            lines.append("")
            lines.append("🗑️ 今日作废（超过7天未完成）")
            for log in retired:
                lines.append(f"- {log.serial_number}")

        try:
            send_group_webhook_message(webhook_url, "\n".join(lines))
        except RuntimeError as e:
            print(f"[uchoice_daily] digest push failed for group {group_id}: {e}", flush=True)


def _run_storage_fee_ledger(db: DBSession) -> None:
    today = date.today()
    for warehouse_code in WAREHOUSES:
        total_pallets = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code)
            .with_entities(UchoiceStorage.pallet_count)
            .all()
        )
        pallet_sum = sum((row[0] for row in total_pallets), 0)
        fee = Decimal(pallet_sum) * Decimal(STORAGE_PER_PALLET_PER_DAY)

        existing = db.query(UchoiceStorageFeeLedger).filter_by(
            warehouse_code=warehouse_code, fee_date=today
        ).first()
        if existing:
            existing.pallet_count = pallet_sum
            existing.storage_fee = fee
        else:
            db.add(UchoiceStorageFeeLedger(
                warehouse_code=warehouse_code, fee_date=today,
                pallet_count=pallet_sum, storage_fee=fee
            ))
        db.commit()


def _webhook_url_for_group(db: DBSession, group_id) -> str | None:
    if group_id is None:
        return None
    from models.group import GroupConfig
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    return group.group_robot_webhook_url if group else None
