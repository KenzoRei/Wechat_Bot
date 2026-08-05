"""
Pre-confirmation business-rule checks — run right before a confirmation
template would be built, so a request that was always going to fail doesn't
get shown a confirmation prompt at all. Registry-with-default-fallback,
mirroring handlers/registry.py's idiom; most services never need an entry.
"""
from functools import partial

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


_COMPLETION_LINE_FIELDS = {
    "confirm_outbound_completion": ("fulfillment_lines", "发货"),
    "confirm_inbound_completion":  ("received_lines", "收货"),
}


def _loose_line_restatement_required(
    service_name: str, context: dict, collected_fields: dict, db: DBSession
) -> str | None:
    """
    confirm_inbound_completion / confirm_outbound_completion: if the
    original request had a loose (box_count) line, it has no sensible
    default and MUST be restated as a source/resulting boxes_per_pallet
    conversion pair before this can proceed — see
    ApplyInboundStorageHandler/ApplyOutboundStorageHandler's identical
    guards. This can't be left to the AI to reliably ask about on its own
    (observed live: it didn't, 0/4 trials) — and the deterministic
    single-candidate auto-resolve in workflow_engine.py forces
    all_fields_collected=true regardless of what the AI decided, so
    without this check a loose line sails straight through to execution,
    crashes, and permanently fails the original request (a completion
    target must be status='processing' to be resolved again — there is no
    retry once it's failed).
    """
    restated_key, verb = _COMPLETION_LINE_FIELDS[service_name]

    reference_serial = collected_fields.get("reference_serial")
    if not reference_serial:
        return None

    from core.uchoice_context import resolve_completion_target, sku_label_map

    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        return None

    loose_skus = {l["sku_code"] for l in (original_fields.get("sku_lines") or []) if "box_count" in l}
    if not loose_skus:
        return None

    restated_lines = collected_fields.get(restated_key) or []
    converted_skus = {
        l["sku_code"] for l in restated_lines
        if "source_boxes_per_pallet" in l and "resulting_boxes_per_pallet" in l
    }

    missing = loose_skus - converted_skus
    if not missing:
        return None

    sku_labels = sku_label_map(db)
    missing_labels = "、".join(sku_labels.get(s, s) for s in sorted(missing))
    return (
        f"商品 {missing_labels} 是散箱{verb}，系统没有默认值可用，"
        f"请说明是从多少箱/托的托盘取货的、取货后该托盘还剩多少箱（例如\"64箱的托盘，剩44箱\"）。"
    )


PRE_CONFIRM_VALIDATORS = {
    "role_change": _last_admin_protection,
    "confirm_outbound_completion": partial(_loose_line_restatement_required, "confirm_outbound_completion"),
    "confirm_inbound_completion": partial(_loose_line_restatement_required, "confirm_inbound_completion"),
}


def run(service_type_name: str, context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """Returns an error message if the request should be blocked, else None."""
    validator = PRE_CONFIRM_VALIDATORS.get(service_type_name)
    if validator is None:
        return None
    return validator(context, collected_fields, db)
