"""
Pre-confirmation business-rule checks — run right before a confirmation
template would be built, so a request that was always going to fail doesn't
get shown a confirmation prompt at all. Registry-with-default-fallback,
mirroring handlers/registry.py's idiom; most services never need an entry.
"""
from sqlalchemy.orm import Session as DBSession
from models.group import GroupMember
from models.role import Role


def _last_admin_protection(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    role_change: reject demoting the group's only remaining active admin.
    Promotions (new_role == 'admin') never trip this check.
    """
    if collected_fields.get("new_role") == "admin":
        return None

    target_openid = collected_fields.get("target_openid")
    if not target_openid:
        return None

    group_id = context["group_id"]
    target_member = db.query(GroupMember).filter_by(
        wechat_openid=target_openid, group_id=group_id
    ).first()
    if not target_member:
        return None

    target_role = db.query(Role).filter_by(role_id=target_member.role_id).first()
    if not target_role or target_role.name != "admin":
        return None  # target isn't currently an admin — nothing to protect

    admin_role = db.query(Role).filter_by(name="admin").first()
    if not admin_role:
        return None

    active_admin_count = db.query(GroupMember).filter_by(
        group_id=group_id, role_id=admin_role.role_id, is_active=True
    ).count()
    if active_admin_count <= 1:
        return "无法将该成员的角色改为非管理员——该群组当前仅剩一名管理员。"
    return None


PRE_CONFIRM_VALIDATORS = {
    "role_change": _last_admin_protection,
}


def run(service_type_name: str, context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """Returns an error message if the request should be blocked, else None."""
    validator = PRE_CONFIRM_VALIDATORS.get(service_type_name)
    if validator is None:
        return None
    return validator(context, collected_fields, db)
