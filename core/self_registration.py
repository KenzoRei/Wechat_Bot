"""
Self-registration is a
deterministic pre-access system command, not an AI-routed business service --
it does not participate in the normal service/workflow/confirmation
machinery, and never touches conversation_session or request_log.

Two independent entry points, called from api/webhook.py:

- try_handle_registration_command: runs BEFORE access_control.check_access,
  with its own group/membership lookup. This is the only place the exact
  command is recognized; there is no second, post-access copy of this check.
  Handles both brand-new registration and a retry by an
  already-registered sender (pending or operational).
- pending_short_circuit_reply: runs AFTER check_access succeeds, only for
  non-command messages from a sender whose resolved role is "pending".
"""
import unicodedata

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

REGISTRATION_COMMAND = "注册成员"

_ALREADY_PENDING_REPLY = "您已注册，正在等待管理员分配角色。"
_ALREADY_OPERATIONAL_REPLY = "您已经是本群组成员。"
_REGISTERED_REPLY = "注册成功，正在等待管理员为您分配角色。"
_FAILED_REPLY = "注册失败，请稍后重试。"
PENDING_SHORT_CIRCUIT_REPLY = "您的注册申请正在等待管理员分配角色，暂时无法使用其他功能。"


def _normalize(text: str) -> str:
    """NFKC plus outer-whitespace stripping; exact
    equality is checked against this normalized form. Bot-mention prefixes
    are already stripped upstream by core/webhook_receiver.py."""
    return unicodedata.normalize("NFKC", text or "").strip()


def is_registration_command(content: str) -> bool:
    return _normalize(content) == REGISTRATION_COMMAND


def try_handle_registration_command(db: DBSession, message: dict) -> str | None:
    """
    Returns the reply string if this message was the exact registration
    command (regardless of outcome) -- caller must send it and stop, never
    falling through to check_access/session/AI for this turn. Returns None
    if the message doesn't qualify as the command at all, in which case the
    caller proceeds with the normal access-control path unchanged.
    """
    if message.get("chat_type") != "group" or not message.get("group_id"):
        return None
    wechat_openid = message.get("from_user") or ""
    if not wechat_openid:
        return None
    if not is_registration_command(message.get("content", "")):
        return None

    from models.group import GroupConfig

    group = db.query(GroupConfig).filter_by(
        wechat_group_id=message["group_id"], is_active=True
    ).first()
    if group is None:
        # Unknown/inactive group -- silent, matches access_control.check_access's
        # own AccessDenied(notify_user=False) for the identical condition.
        return None

    return _register(db, wechat_openid, group.group_id)


def _register(db: DBSession, wechat_openid: str, group_id) -> str:
    from models.group import GroupMember
    from models.role import Role

    existing = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid, group_id=group_id
    ).first()
    if existing is not None:
        role = db.query(Role).filter_by(role_id=existing.role_id).first()
        if role is not None and role.name == "pending":
            return _ALREADY_PENDING_REPLY
        return _ALREADY_OPERATIONAL_REPLY

    pending_role = db.query(Role).filter_by(name="pending").first()
    if pending_role is None:
        print("[self_registration] 'pending' role missing from role table -- migration V7 not applied?", flush=True)
        db.rollback()
        return _FAILED_REPLY

    db.add(GroupMember(wechat_openid=wechat_openid, group_id=group_id, role_id=pending_role.role_id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Only the composite-PK violation on group_member's
        # (wechat_openid, group_id) counts as a duplicate.
        # Everything else (FK violation, other integrity error) is a real
        # failure, not a false "already registered" success.
        constraint_name = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
        if constraint_name == "group_member_pkey":
            return _ALREADY_PENDING_REPLY
        print(f"[self_registration] registration insert failed (not a duplicate): {exc}", flush=True)
        return _FAILED_REPLY
    except SQLAlchemyError as exc:
        # Any other database failure (dropped connection, timeout, etc.) --
        # not a duplicate, must not be reported as success. Roll back and
        # fail controlled, same as the non-duplicate IntegrityError branch.
        db.rollback()
        print(f"[self_registration] registration insert failed (database error): {exc}", flush=True)
        return _FAILED_REPLY

    return _REGISTERED_REPLY


def pending_short_circuit_reply(role_name: str) -> str | None:
    """
    Called after access_control.check_access succeeds, only for the
    non-command path (the command itself is always handled by
    try_handle_registration_command before check_access even runs).
    """
    if role_name == "pending":
        return PENDING_SHORT_CIRCUIT_REPLY
    return None
