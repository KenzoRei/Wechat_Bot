from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession

from models.session import ConversationSession
from clients.wechat_client import send_message


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
    """
    session.status     = "timed_out"
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    try:
        send_message(
            session.wechat_openid,
            "您的申请因长时间未操作已自动取消。如需继续，请重新发起申请。"
        )
    except Exception:
        # notification failure must not crash the job —
        # session is already closed in DB regardless
        pass
