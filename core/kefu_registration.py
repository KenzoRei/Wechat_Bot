"""
Kefu-side self-registration -- kefu-migration-plan.md Sec 2.3 / Codex
round-88 finding 1. Mirrors core/self_registration.py's exact pattern
(deterministic pre-access system command, own membership lookup, pending
role, admin promotes later) but against kefu_staff instead of
group_member, since Kefu's 1:1-per-account model doesn't fit the WeCom
group-shaped table. group_id is never chosen by the staff member or
inferred from anything about the message -- it is always
config.KEFU_GROUP_ID, the single fixed open_kfid -> group_id mapping
this deployment is configured with (Sec 2.3's explicit one-tenant scope
limit for this migration).
"""
import unicodedata

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from core.kefu_contracts import KefuIdentity

REGISTRATION_COMMAND = "注册成员"

_ALREADY_PENDING_REPLY = "您已注册，正在等待管理员分配角色。"
_ALREADY_OPERATIONAL_REPLY = "您已经是本群组成员。"
_REGISTERED_REPLY = "注册成功，正在等待管理员为您分配角色。"
_FAILED_REPLY = "注册失败，请稍后重试。"
PENDING_SHORT_CIRCUIT_REPLY = "您的注册申请正在等待管理员分配角色，暂时无法使用其他功能。"


def _normalize(text: str) -> str:
    """Same normalization as core/self_registration.py -- NFKC + strip, exact-equality match."""
    return unicodedata.normalize("NFKC", text or "").strip()


def is_registration_command(content: str) -> bool:
    return _normalize(content) == REGISTRATION_COMMAND


def try_handle_kefu_registration_command(db: DBSession, identity: KefuIdentity, content: str) -> str | None:
    """
    Returns the reply string if this message was the exact registration
    command (regardless of outcome) -- caller must send it and stop, never
    falling through to check_kefu_access/case processing for this turn.
    Returns None if the message doesn't qualify, in which case the caller
    proceeds with the normal access-control path unchanged.
    """
    if not is_registration_command(content or ""):
        return None
    return _register(db, identity)


def _register(db: DBSession, identity: KefuIdentity) -> str:
    import config
    from models.kefu import KefuStaff
    from models.role import Role

    existing = db.query(KefuStaff).filter_by(
        open_kfid=identity.open_kfid, external_userid=identity.external_userid
    ).first()
    if existing is not None:
        role = db.query(Role).filter_by(role_id=existing.role_id).first()
        if role is not None and role.name == "pending":
            return _ALREADY_PENDING_REPLY
        return _ALREADY_OPERATIONAL_REPLY

    pending_role = db.query(Role).filter_by(name="pending").first()
    if pending_role is None:
        print("[kefu_registration] 'pending' role missing from role table -- migration V7 not applied?", flush=True)
        db.rollback()
        return _FAILED_REPLY

    db.add(KefuStaff(
        open_kfid=identity.open_kfid,
        external_userid=identity.external_userid,
        group_id=config.KEFU_GROUP_ID,
        role_id=pending_role.role_id,
    ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Same discipline as core/self_registration.py's round-39 finding 3:
        # only the composite-uniqueness violation on kefu_staff's
        # (open_kfid, external_userid) counts as a duplicate. Anything else
        # (FK violation, other integrity error) is a real failure, not a
        # false "already registered" success.
        constraint_name = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
        if constraint_name == "kefu_staff_open_kfid_external_userid_key":
            return _ALREADY_PENDING_REPLY
        print(f"[kefu_registration] registration insert failed (not a duplicate): {exc}", flush=True)
        return _FAILED_REPLY
    except SQLAlchemyError as exc:
        db.rollback()
        print(f"[kefu_registration] registration insert failed (database error): {exc}", flush=True)
        return _FAILED_REPLY

    return _REGISTERED_REPLY


def pending_short_circuit_reply(role_name: str) -> str | None:
    """Same contract as core/self_registration.py's function of the same name."""
    if role_name == "pending":
        return PENDING_SHORT_CIRCUIT_REPLY
    return None
