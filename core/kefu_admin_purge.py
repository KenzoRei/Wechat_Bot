"""
Deterministic, pre-AI admin command to close every in-progress Kefu case at
once. Modeled on core/kefu_registration.py's exact-phrase pre-access command
pattern: recognized by literal string match only, never AI intent
classification, so a destructive reset can never be triggered by
interpretation.

Deliberately stateless across the two phrases -- neither ConversationSession
nor KefuStaffCaseContext is ever touched by the trigger/confirm EXCHANGE
itself (only by the actual purge, which is the intended effect of the
confirm phrase). Each phrase is independently self-sufficient (the confirm
phrase works standalone, without requiring the trigger phrase first), so
there is no "awaiting confirmation" state to persist, nothing for this
command to conflict-check against, and no session-restoration bookkeeping
needed either -- whatever the admin already has open is left completely
alone unless/until they actually confirm.
"""
import unicodedata
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

TRIGGER_COMMAND = "清空所有进行中会话"
CONFIRM_COMMAND = "确认清空所有进行中会话"

_OPEN_STATUSES = ("active", "pending_confirmation")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def is_purge_trigger(content: str) -> bool:
    return _normalize(content) == TRIGGER_COMMAND


def is_purge_confirm(content: str) -> bool:
    return _normalize(content) == CONFIRM_COMMAND


def _open_kefu_sessions(db: DBSession):
    from models.session import ConversationSession
    return (
        db.query(ConversationSession)
        .filter(
            ConversationSession.source_channel == "kefu",
            ConversationSession.status.in_(_OPEN_STATUSES),
        )
        .all()
    )


def try_handle_purge_command(db: DBSession, role: str, content: str) -> str | None:
    """
    Returns the reply string if this message was one of the two exact purge
    commands (regardless of outcome) -- caller must send it and stop, never
    falling through to AI/case processing for this turn. Returns None if the
    message doesn't qualify, in which case the caller proceeds with the
    normal pipeline unchanged.

    Admin-only: a non-admin sending either exact phrase falls through to
    normal processing (deny-by-default, same posture as every other
    service -- there is no grant to bypass here since this never goes
    through the group_service/group_service_role machinery at all).
    """
    if role != "admin":
        return None
    if is_purge_trigger(content):
        count = len(_open_kefu_sessions(db))
        if count == 0:
            return "当前没有进行中的会话，无需清空。"
        return (
            f"即将关闭 {count} 个进行中的会话及其未完成的申请记录，此操作不可撤销，"
            f"不影响历史记录、库存、地址簿或客户资料。\n如需执行，请精确发送：{CONFIRM_COMMAND}"
        )
    if is_purge_confirm(content):
        closed = _run_purge(db)
        if closed == 0:
            return "当前没有进行中的会话，无需清空。"
        return f"已清空 {closed} 个进行中的会话。"
    return None


def _run_purge(db: DBSession) -> int:
    """
    Closes every open Kefu session and its OWNED request_log, mirroring
    jobs/session_expiry.py's owns_log logic exactly: a targets_existing_
    request session's log belongs to the ORIGINAL request it references,
    not this session, and must be left alone. Clears every stale
    KefuStaffCaseContext binding pointing at a closed session so no staff
    is left pointing at a session that no longer exists (functionally
    redundant given _resolve_kefu_session re-validates live status, but
    keeps the table honest). Never touches uchoice_customer/uchoice_
    address, storage, invoices, or any already-terminal request_log/
    session -- only rows currently in _OPEN_STATUSES.
    """
    from models.service import ServiceType
    from models.kefu import KefuStaffCaseContext
    from models.request_log import RequestLog

    sessions = _open_kefu_sessions(db)
    if not sessions:
        return 0

    now = datetime.now(timezone.utc)
    session_ids = [s.session_id for s in sessions]

    for session in sessions:
        session.status = "cancelled"
        session.updated_at = now
        if session.request_log_id:
            owns_log = True
            if session.service_type_id:
                service_type = db.get(ServiceType, session.service_type_id)
                if service_type and service_type.targets_existing_request:
                    owns_log = False
            if owns_log:
                log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
                if log is not None and log.status in ("pending", "processing"):
                    log.status = "cancelled"
                    log.completed_at = now

    db.query(KefuStaffCaseContext).filter(
        KefuStaffCaseContext.active_session_id.in_(session_ids)
    ).update({"active_session_id": None}, synchronize_session="fetch")

    db.commit()
    return len(sessions)
