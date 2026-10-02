"""
A Kefu outbound draft parked while a new destination address is added
(address-pivot plan rev 3, item 3).

The two sessions point at each other through collected_fields:
  outbound  _parked_for_address_session_id -> the open address session
  address   _resume_outbound_session_id    -> the parked outbound session

A parked outbound stays 'active' (its request stays 'pending' and keeps its
serial number), but no turn may act on it directly while its address
session is open: core/kefu_case_adapter.py redirects such turns to the
address session. It ends in exactly one of these ways, each in the same
transaction as the event that causes it:
  - the address is saved: resumed (link removed), or closed when it can't be
  - the address step is cancelled: cancelled with it
  - the address session times out: timed out with it
Every path that changes a parked outbound locks its row and re-checks the
link first (still_parked_for).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session as DBSession

PARKED_KEY = "_parked_for_address_session_id"
RESUME_KEY = "_resume_outbound_session_id"

_OPEN = ("active", "pending_confirmation")


def parked_for(session) -> str | None:
    """The address session id a parked outbound waits on, else None."""
    return (session.collected_fields or {}).get(PARKED_KEY) if session is not None else None


def resumes(session) -> str | None:
    """The parked outbound session id an address session will resume, else None."""
    return (session.collected_fields or {}).get(RESUME_KEY) if session is not None else None


def lock_session(db: DBSession, session_id):
    """SELECT ... FOR UPDATE, refreshing any identity-mapped copy."""
    from models.session import ConversationSession

    if not session_id:
        return None
    # populate_existing() overwrites the identity-mapped object with the
    # row's database state; flush first so this turn's own unflushed
    # changes to it are not lost (the session factory doesn't autoflush).
    db.flush()
    return (
        db.query(ConversationSession)
        .filter(ConversationSession.session_id == UUID(str(session_id)))
        .populate_existing()
        .with_for_update()
        .first()
    )


def still_parked_for(outbound, address_session_id) -> bool:
    return (
        outbound is not None
        and outbound.status in _OPEN
        and parked_for(outbound) == str(address_session_id)
    )


def is_open(session) -> bool:
    return session is not None and session.status in _OPEN


def close(db: DBSession, outbound, status: str) -> None:
    """Close a parked outbound and its own request ('cancelled' or 'timed_out')."""
    from models.request_log import RequestLog

    now = datetime.now(timezone.utc)
    outbound.status = status
    outbound.updated_at = now
    if outbound.request_log_id:
        log = db.get(RequestLog, outbound.request_log_id)
        if log is not None and log.status in ("pending", "processing"):
            log.status = status
            if status == "timed_out":
                log.completed_at = now


def unpark(outbound) -> None:
    outbound.collected_fields = {k: v for k, v in (outbound.collected_fields or {}).items() if k != PARKED_KEY}


def follow_expiry(db: DBSession, address_session) -> None:
    """Keep a parked outbound alive exactly as long as its address session."""
    outbound_id = resumes(address_session)
    if not outbound_id or address_session.status not in _OPEN:
        return
    outbound = lock_session(db, outbound_id)
    if still_parked_for(outbound, address_session.session_id):
        outbound.expires_at = address_session.expires_at


def serial_number(db: DBSession, session) -> str:
    from models.request_log import RequestLog

    if session is None or not session.request_log_id:
        return ""
    log = db.get(RequestLog, session.request_log_id)
    return log.serial_number if log is not None else ""
