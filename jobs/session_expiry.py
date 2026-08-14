from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession

from models.session import ConversationSession
from models.service import ServiceType
from models.group import GroupConfig
from models.kefu import KefuStaff
from core import request_logger
from core.kefu_delivery import enqueue_text
from clients.wechat_client import send_group_webhook_message


def run_expiry_check(db: DBSession) -> None:
    """
    Finds all sessions that have passed their expires_at timestamp
    and closes them as timed_out.

    Scheduled to run every 5 minutes via APScheduler (wired up in main.py).
    Covers all in-progress statuses: active and pending_confirmation.
    """
    now = datetime.now(timezone.utc)

    expired = db.query(ConversationSession).filter(
        ConversationSession.status.in_(['active', 'pending_confirmation']),
        ConversationSession.expires_at <= now
    ).all()

    for session in expired:
        _expire_session(db, session)


def _expire_session(db: DBSession, session: ConversationSession) -> None:
    """
    Closes one expired session and notifies the user.

    Since request_log rows are now created at new_request time (not at
    confirmation), an abandoned session can leave its log stuck at 'pending'
    forever unless we also close it out here — except for
    targets_existing_request services (e.g. confirm_inbound_completion),
    where session.request_log_id points at the ORIGINAL request being
    referenced, not one this session owns; that log must be left alone
    (still legitimately 'processing', eventually swept by the daily
    stale-retirement job if truly abandoned).
    """
    session.status     = "timed_out"
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    if session.request_log_id:
        owns_log = True
        if session.service_type_id:
            service_type = db.query(ServiceType).filter_by(
                service_type_id=session.service_type_id
            ).first()
            if service_type and service_type.targets_existing_request:
                owns_log = False
        if owns_log:
            request_logger.mark_timed_out(db, session.request_log_id)

    # Notification is channel-aware; the state transition above applies to both
    # channels identically -- only how (and whether) the owner is told
    # branches by source_channel. Kefu must never fall through to the
    # Smart Robot group-webhook path, which assumes wechat_openid and would
    # otherwise post "用户ID：None" for a Kefu session.
    if session.source_channel == "kefu":
        _notify_kefu_expiry(db, session)
        return

    # response_url is single-use and tied to the message that triggered it —
    # it isn't stored on the session and would likely be stale by the time a
    # session actually expires anyway. group_robot_webhook_url is persistent,
    # so use it instead. Groups without one configured (e.g. FedEx/UPS groups
    # that predate it) simply get no notification, same as before this fix.
    group = db.query(GroupConfig).filter_by(group_id=session.group_id).first()
    webhook_url = group.group_robot_webhook_url if group else None
    if not webhook_url:
        return

    try:
        send_group_webhook_message(
            webhook_url,
            f"您的申请（用户ID：{session.wechat_openid}）因长时间未操作已自动取消。如需继续，请重新发起申请。"
        )
    except Exception:
        # notification failure must not crash the job —
        # session is already closed in DB regardless
        pass


def _notify_kefu_expiry(db: DBSession, session: ConversationSession) -> None:
    """
    Kefu's durable outbound queue (core/kefu_delivery.py), not a Smart-Robot-
    style webhook -- delivered whenever the staff member's reply window is
    next open, same as any other Kefu reply. No group-wide broadcast exists
    for Kefu (each case belongs to one staff member), so a session with no
    bound staff member (opened_by_staff_id is None, shouldn't happen in
    practice but is not guaranteed by a DB constraint) is deliberately
    suppressed rather than guessed at; this is an interim business rule, not
    an error.
    """
    if session.opened_by_staff_id is None:
        return
    staff = db.query(KefuStaff).filter_by(staff_id=session.opened_by_staff_id).first()
    if staff is None or not staff.is_active:
        return

    try:
        enqueue_text(
            db,
            recipient_staff_id=staff.staff_id,
            idempotency_key=f"session-expiry:{session.session_id}",
            text_content="本次会话因长时间未操作已自动取消。如需继续，请重新发起申请。",
            session_id=session.session_id,
        )
        db.commit()
    except Exception:
        # notification failure must not crash the job —
        # session is already closed in DB regardless
        db.rollback()
