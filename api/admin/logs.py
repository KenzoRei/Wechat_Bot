from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timezone, timedelta

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.request_log import RequestLog
from models.group import GroupMember
from models.service import ServiceType
from api.schemas import RequestLogSummary, RequestLogDetail

router = APIRouter(prefix="/admin/request-logs", dependencies=[Depends(verify_admin_key)])


def _build_query(db: Session, status=None, group_id=None, date_from=None, date_to=None):
    """Builds the base query with joins and optional filters."""
    q = (
        db.query(RequestLog, GroupMember.display_name, ServiceType.name.label("service_name"))
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == RequestLog.wechat_openid,
            GroupMember.group_id == RequestLog.group_id
        ))
        .outerjoin(ServiceType, ServiceType.service_type_id == RequestLog.service_type_id)
    )

    if status:
        q = q.filter(RequestLog.status == status)
    if group_id:
        q = q.filter(RequestLog.group_id == group_id)
    if date_from:
        q = q.filter(RequestLog.created_at >= date_from)
    if date_to:
        q = q.filter(RequestLog.created_at <= date_to)

    return q.order_by(RequestLog.created_at.desc())


@router.get("")
def list_logs(
    status:     str | None = Query(None),
    group_id:   str | None = Query(None),
    date_from:  str | None = Query(None),
    date_to:    str | None = Query(None),
    db: Session = Depends(get_db)
):
    # default date_from to 30 days ago if not provided
    if date_from:
        df = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    else:
        df = datetime.now(timezone.utc) - timedelta(days=30)

    dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) if date_to else None

    rows = _build_query(db, status=status, group_id=group_id, date_from=df, date_to=dt).all()

    return {"data": [
        RequestLogSummary(
            log_id=log.log_id,
            serial_number=log.serial_number,
            wechat_openid=log.wechat_openid,
            display_name=display_name,
            group_id=log.group_id,
            service_name=service_name,
            status=log.status,
            created_at=log.created_at,
            completed_at=log.completed_at,
        )
        for log, display_name, service_name in rows
    ]}


@router.get("/{serial_number}")
def get_log(serial_number: str, db: Session = Depends(get_db)):
    row = (
        db.query(RequestLog, GroupMember.display_name, ServiceType.name.label("service_name"))
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == RequestLog.wechat_openid,
            GroupMember.group_id == RequestLog.group_id
        ))
        .outerjoin(ServiceType, ServiceType.service_type_id == RequestLog.service_type_id)
        .filter(RequestLog.serial_number == serial_number)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Serial number not found")

    log, display_name, service_name = row

    return {"data": RequestLogDetail(
        log_id=log.log_id,
        serial_number=log.serial_number,
        wechat_openid=log.wechat_openid,
        display_name=display_name,
        group_id=log.group_id,
        service_name=service_name,
        status=log.status,
        created_at=str(log.created_at),
        completed_at=str(log.completed_at) if log.completed_at else None,
        workflow_name=None,     # v1: not joined — add in v2 if needed
        raw_message=log.raw_message,
        parsed_input=log.parsed_input,
        result=log.result,
        error_detail=log.error_detail,
    )}
