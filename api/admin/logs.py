import base64
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.request_log import RequestLog
from models.group import GroupMember
from models.kefu import KefuStaff
from models.service import ServiceType
from api.schemas import RequestLogSummary, RequestLogDetail, RequestLogSession, SessionActor

router = APIRouter(prefix="/admin/request-logs", dependencies=[Depends(verify_admin_key)])

_VALID_STATUSES = {"pending", "processing", "success", "failed", "cancelled", "stale"}
_VALID_CHANNELS = {"smart_robot", "kefu"}
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


def _parse_datetime_utc(value: str, field_name: str) -> datetime:
    """
    Requires a full ISO-8601 datetime (not a bare date, which is ambiguous
    about whose midnight it means). A naive value is interpreted as UTC. An
    offset-aware value is CONVERTED to UTC via astimezone(), never relabeled
    via replace(tzinfo=...) -- replace() silently discards the original
    offset and keeps the same wall-clock numbers, which is wrong by however
    many hours the offset was (e.g. 2026-09-03T18:00:00-04:00 is really
    22:00:00 UTC, but .replace(tzinfo=utc) would produce 18:00:00 UTC).
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {value!r} -- expected ISO-8601 datetime")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _encode_cursor(created_at: datetime, log_id) -> str:
    payload = json.dumps([created_at.isoformat(), str(log_id)])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, "UUID"]:
    """
    A cursor that is valid base64/JSON/ISO-datetime but carries a garbage
    log_id is still "structurally valid" by those checks alone -- it used
    to sail through to the keyset query, where PostgreSQL itself rejects
    a non-UUID value in a uuid comparison, surfacing as an uncaught 500
    instead of a clean 400. Validating log_id as a real UUID here (and
    catching that failure in the same except) closes that gap.
    """
    from uuid import UUID
    try:
        created_at_str, log_id_str = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return datetime.fromisoformat(created_at_str), UUID(log_id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


def _build_query(db: Session, status=None, group_id=None, source_channel=None, date_from=None, date_to=None):
    q = (
        db.query(RequestLog, GroupMember.display_name.label("member_display_name"),
                  KefuStaff.display_name.label("staff_display_name"), ServiceType.name.label("service_name"))
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == RequestLog.wechat_openid,
            GroupMember.group_id == RequestLog.group_id
        ))
        .outerjoin(KefuStaff, KefuStaff.staff_id == RequestLog.submitted_by_staff_id)
        .outerjoin(ServiceType, ServiceType.service_type_id == RequestLog.service_type_id)
    )

    if status:
        q = q.filter(RequestLog.status == status)
    if group_id:
        q = q.filter(RequestLog.group_id == group_id)
    if source_channel:
        q = q.filter(RequestLog.source_channel == source_channel)
    if date_from:
        q = q.filter(RequestLog.created_at >= date_from)
    if date_to:
        q = q.filter(RequestLog.created_at <= date_to)

    return q


def _to_summary(log, member_display_name, staff_display_name, service_name) -> RequestLogSummary:
    return RequestLogSummary(
        log_id=log.log_id,
        serial_number=log.serial_number,
        wechat_openid=log.wechat_openid,
        display_name=member_display_name or staff_display_name,
        group_id=log.group_id,
        service_name=service_name,
        source_channel=log.source_channel,
        status=log.status,
        created_at=log.created_at,
        completed_at=log.completed_at,
    )


@router.get("")
def list_logs(
    status:          str | None = Query(None),
    group_id:        str | None = Query(None),
    source_channel:  str | None = Query(None),
    date_from:       str | None = Query(None),
    date_to:         str | None = Query(None),
    cursor:          str | None = Query(None),
    page_size:       int = Query(_DEFAULT_PAGE_SIZE, ge=1),
    db: Session = Depends(get_db),
):
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status!r}. Allowed: {sorted(_VALID_STATUSES)}")
    if source_channel is not None and source_channel not in _VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Invalid source_channel: {source_channel!r}. Allowed: {sorted(_VALID_CHANNELS)}")
    if page_size > _MAX_PAGE_SIZE:
        raise HTTPException(status_code=400, detail=f"page_size must be <= {_MAX_PAGE_SIZE}")

    # Default 30-day window when date_from is omitted, preserved from the
    # original implementation.
    df = _parse_datetime_utc(date_from, "date_from") if date_from else datetime.now(timezone.utc) - timedelta(days=30)
    dt = _parse_datetime_utc(date_to, "date_to") if date_to else None
    if dt is not None and df > dt:
        raise HTTPException(status_code=400, detail="date_from must not be after date_to")

    q = _build_query(db, status=status, group_id=group_id, source_channel=source_channel, date_from=df, date_to=dt)

    if cursor:
        cursor_created_at, cursor_log_id = _decode_cursor(cursor)
        q = q.filter(
            (RequestLog.created_at < cursor_created_at)
            | ((RequestLog.created_at == cursor_created_at) & (RequestLog.log_id < cursor_log_id))
        )

    q = q.order_by(RequestLog.created_at.desc(), RequestLog.log_id.desc())
    rows = q.limit(page_size + 1).all()

    next_cursor = None
    if len(rows) > page_size:
        # The probe row exists only to prove another page exists -- it is
        # never itself returned. next_cursor is built from the LAST ROW
        # ACTUALLY RETURNED (page[-1]), not the probe row: encoding the
        # probe row would permanently skip it, since the following page's
        # "created_at < cursor" would then exclude it too, and no page ever
        # returns it.
        page = rows[:page_size]
        last_returned = page[-1][0]
        next_cursor = _encode_cursor(last_returned.created_at, last_returned.log_id)
    else:
        page = rows

    return {
        "data": [_to_summary(log, member_dn, staff_dn, service_name) for log, member_dn, staff_dn, service_name in page],
        "next_cursor": next_cursor,
    }


@router.get("/{serial_number}")
def get_log(serial_number: str, db: Session = Depends(get_db)):
    row = (
        db.query(RequestLog, GroupMember.display_name.label("member_display_name"),
                  KefuStaff.display_name.label("staff_display_name"), ServiceType.name.label("service_name"))
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == RequestLog.wechat_openid,
            GroupMember.group_id == RequestLog.group_id
        ))
        .outerjoin(KefuStaff, KefuStaff.staff_id == RequestLog.submitted_by_staff_id)
        .outerjoin(ServiceType, ServiceType.service_type_id == RequestLog.service_type_id)
        .filter(RequestLog.serial_number == serial_number)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Serial number not found")

    log, member_display_name, staff_display_name, service_name = row

    return {"data": RequestLogDetail(
        log_id=log.log_id,
        serial_number=log.serial_number,
        wechat_openid=log.wechat_openid,
        display_name=member_display_name or staff_display_name,
        group_id=log.group_id,
        service_name=service_name,
        source_channel=log.source_channel,
        status=log.status,
        created_at=log.created_at,
        completed_at=log.completed_at,
        workflow_name=None,     # v1: not joined -- add in v2 if needed
        raw_message=log.raw_message,
        parsed_input=log.parsed_input,
        result=log.result,
        error_detail=log.error_detail,
        sessions=_load_sessions(db, log.log_id),
    )}


def _load_sessions(db: Session, log_id) -> list[RequestLogSession]:
    """
    Every session that ever touched this request, in creation order --
    queried by request_log_id, NOT origin_session_id (which only ever
    identifies the *first* session). A targets_existing_request row can
    legitimately have more than one session over its life (confirmed live:
    a create session plus separate later completion attempts).
    """
    from models.session import ConversationSession

    sessions = (
        db.query(ConversationSession, ServiceType.name.label("service_name"),
                  GroupMember.display_name.label("member_display_name"),
                  KefuStaff.display_name.label("staff_display_name"))
        .outerjoin(ServiceType, ServiceType.service_type_id == ConversationSession.service_type_id)
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == ConversationSession.wechat_openid,
            GroupMember.group_id == ConversationSession.group_id,
        ))
        .outerjoin(KefuStaff, KefuStaff.staff_id == ConversationSession.opened_by_staff_id)
        .filter(ConversationSession.request_log_id == log_id)
        .order_by(ConversationSession.created_at.asc(), ConversationSession.session_id.asc())
        .all()
    )

    result = []
    for session, service_name, member_display_name, staff_display_name in sessions:
        if session.source_channel == "kefu":
            actor = SessionActor(
                kind="kefu_staff",
                id=str(session.opened_by_staff_id) if session.opened_by_staff_id else None,
                display_name=staff_display_name,
            )
        else:
            actor = SessionActor(
                kind="group_member",
                id=session.wechat_openid,
                display_name=member_display_name,
            )
        result.append(RequestLogSession(
            session_id=session.session_id,
            service_name=service_name,
            status=session.status,
            source_channel=session.source_channel,
            actor=actor,
            created_at=session.created_at,
            updated_at=session.updated_at,
            conversation_history=session.conversation_history or [],
        ))
    return result
