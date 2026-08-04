import re
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session as DBSession
from models.session import ConversationSession
from models.request_log import RequestLog
from core.access_control import AccessResult
from core import uchoice_context
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
    db: DBSession,
    access: AccessResult,
    session: ConversationSession | None,
    message: dict
) -> dict:
    """Assembles the full context dict passed through the entire pipeline."""
    return {
        # from access_control
        "wechat_openid":     access.wechat_openid,
        "group_id":          str(access.group_id),
        "role":              access.role,
        "display_name":      access.display_name,
        "warehouse_code":    access.warehouse_code,
        "allowed_services":  access.allowed_services,
        "group_context":     access.group_context,
        "group_description": access.group_description,

        # from session (None if not yet created)
        "session_id":           str(session.session_id) if session else None,
        "session_status":       session.status if session else None,
        "serial_number":        None,
        "service_type_id":      str(session.service_type_id) if session and session.service_type_id else None,
        "conversation_history": session.conversation_history if session else [],
        "collected_fields":     session.collected_fields if session else {},

        # candidate-list context injection (addresses, pending requests,
        # storage buckets, member list) — scoped to whichever services this
        # caller's role can actually trigger
        "uchoice_candidates": _build_uchoice_candidates(db, access, session),

        # from webhook_receiver
        "content":      message["content"],
        "msg_id":       message["msg_id"],
        "response_url": message.get("response_url", ""),

        # filled downstream
        "parsed_input":   None,
        "request_log_id": None,
        "result":         None,
        "error_detail":   None,
    }


def _build_uchoice_candidates(
    db: DBSession,
    access: AccessResult,
    session: ConversationSession | None
) -> dict:
    """
    Conditionally fetches candidate lists based on which service names this
    caller's role can trigger — no point injecting the member list for a
    customer who could never call role_change.
    """
    names = {s["name"] for s in access.allowed_services}
    by_name = {s["name"]: s["service_type_id"] for s in access.allowed_services}
    collected = session.collected_fields if session else {}
    scope_warehouse = collected.get("warehouse_code") or access.warehouse_code

    candidates: dict = {}

    SKU_DEPENDENT_SERVICES = {
        "uchoice_inbound_request", "uchoice_outbound_request",
        "adjust_storage", "recount_storage", "move_storage",
        "view_storage", "view_storage_history",
    }
    if names & SKU_DEPENDENT_SERVICES:
        candidates["skus"] = uchoice_context.sku_catalog(db)

    if "uchoice_outbound_request" in names:
        candidates["addresses"] = uchoice_context.address_candidates(db)
        candidates["storage_buckets"] = uchoice_context.storage_bucket_candidates(db, scope_warehouse)

    if "confirm_inbound_completion" in names and "uchoice_inbound_request" in by_name:
        candidates["pending_inbound_requests"] = uchoice_context.pending_request_candidates(
            db, scope_warehouse, [by_name["uchoice_inbound_request"]]
        )

    if "confirm_outbound_completion" in names and "uchoice_outbound_request" in by_name:
        candidates["pending_outbound_requests"] = uchoice_context.pending_request_candidates(
            db, scope_warehouse, [by_name["uchoice_outbound_request"]]
        )

    if "role_change" in names:
        candidates["members"] = uchoice_context.member_candidates(db, access.group_id)

    return candidates
