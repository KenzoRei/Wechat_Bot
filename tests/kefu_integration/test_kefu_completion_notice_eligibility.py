from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text

from core import kefu_completion_notice
from database import SessionLocal
from models.request_log import RequestLog


def test_successful_read_only_request_is_not_claimed_as_completion_notice():
    db = SessionLocal()
    created = []
    try:
        group_id = db.execute(text("select group_id from group_config order by created_at limit 1")).scalar_one()
        service_ids = dict(db.execute(text(
            "select name, service_type_id from service_type where name in ('view_storage','uchoice_inbound_request')"
        )).all())
        for name in ("view_storage", "uchoice_inbound_request"):
            row = RequestLog(
                wechat_openid=None,
                group_id=group_id,
                service_type_id=service_ids[name],
                status="success",
                raw_message=name,
                source_channel="kefu",
                result={"warehouse_code": "NOTICE-TEST"},
                completed_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.flush()
            created.append(row.log_id)
        db.commit()

        claimed = kefu_completion_notice.lock_pending_completion_notice(
            db, SimpleNamespace(warehouse_codes=["NOTICE-TEST"])
        )
        assert claimed.service_type_id == service_ids["uchoice_inbound_request"]
        db.rollback()
    finally:
        db.rollback()
        if created:
            db.execute(text("delete from request_log where log_id=any(:ids)"), {"ids": created})
            db.commit()
        db.close()
