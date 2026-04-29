import re
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session as DBSession
from models.session import ConversationSession
from models.request_log import RequestLog
from core.access_control import AccessResult
import config

SERIAL_PATTERN = re.compile(r'REQ-\d{8}-\d{6}')


def extract_serial_from_message(content: str) -> str | None:
    match = SERIAL_PATTERN.search(content)
    return match.group(0) if match else None


def find_session_by_serial(db: DBSession, serial_number: str) -> ConversationSession | None:
    log = db.query(RequestLog).filter_by(serial_number=serial_number).first()
    if log is None or log.session_id is None:
        return None
    return db.query(ConversationSession).filter(
        ConversationSession.session_id == log.session_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def find_current_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID
) -> ConversationSession | None:
    """Returns the one in-progress session for this user in this group, or None."""
    return db.query(ConversationSession).filter(
        ConversationSession.wechat_openid == wechat_openid,
        ConversationSession.group_id == group_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def resolve_session(
    db: DBSession,
    access: AccessResult,
    content: str
) -> ConversationSession | None:
    """
    Returns the in-progress session if one exists, else None.
    Serial number fast path first; falls back to user+group lookup.
    AI always decides the final intent — this only loads context.
    """
    serial = extract_serial_from_message(content)
    if serial:
        session = find_session_by_serial(db, serial)
        if session:
            return session

    return find_current_session(db, access.wechat_openid, access.group_id)


def create_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID,
    initial_message: str,
    service_type_id: UUID | None = None
) -> ConversationSession:
    session = ConversationSession(
        wechat_openid=wechat_openid,
        group_id=group_id,
        service_type_id=service_type_id,
        status="active",
        conversation_history=[{"role": "user", "content": initial_message}],
        collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_message(
    db: DBSession,
    session: ConversationSession,
    role: str,
    content: str
) -> None:
    """Appends a message to history and resets the expiry timer."""
    session.conversation_history = session.conversation_history + [
        {"role": role, "content": content}
    ]
    session.updated_at = datetime.now(timezone.utc)
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)
    db.commit()


def update_collected_fields(
    db: DBSession,
    session: ConversationSession,
    fields: dict
) -> None:
    session.collected_fields = {**session.collected_fields, **fields}
    db.commit()


def close_session(
    db: DBSession,
    session: ConversationSession,
    status: str  # "completed" | "cancelled" | "rejected" | "failed" | "timed_out"
) -> None:
    session.status = status
    session.updated_at = datetime.now(timezone.utc)
    db.commit()


def build_context(
    access: AccessResult,
    session: ConversationSession | None,
    message: dict
) -> dict:
    """Assembles the full context dict passed through the entire pipeline."""
    return {
        # from access_control
        "wechat_openid":    access.wechat_openid,
        "group_id":         str(access.group_id),
        "role":             access.role,
        "display_name":     access.display_name,
        "allowed_services": access.allowed_services,

        # from session (None if not yet created)
        "session_id":           str(session.session_id) if session else None,
        "serial_number":        None,
        "service_type_id":      str(session.service_type_id) if session and session.service_type_id else None,
        "conversation_history": session.conversation_history if session else [],
        "collected_fields":     session.collected_fields if session else {},

        # from webhook_receiver
        "content": message["content"],
        "msg_id":  message["msg_id"],

        # filled downstream
        "parsed_input":   None,
        "request_log_id": None,
        "result":         None,
        "error_detail":   None,
    }
