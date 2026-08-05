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


def _loose_outbound_pick_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    confirm_outbound_completion: a loose (box_count) original line gets an
    automatic pick resolved by workflow_engine._resolve_outbound_loose_pick_defaults
    (smallest-boxes_per_pallet buckets first) before this validator runs.
    This only needs to block the rare case where that resolution failed —
    total available stock across every bucket for that sku+warehouse can't
    even cover the requested amount — so a request that was always going to
    crash on insufficient stock doesn't get shown a confirmation prompt at all.
    """
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

    restated_lines = collected_fields.get("fulfillment_lines") or []
    by_sku = {l["sku_code"]: l for l in restated_lines}
    missing = [sku for sku in loose_skus if not (by_sku.get(sku) or {}).get("picks")]
    if not missing:
        return None

    sku_labels = sku_label_map(db)
    missing_labels = "、".join(sku_labels.get(s, s) for s in sorted(missing))
    return (
        f"商品 {missing_labels} 是散箱发货，当前库存不足以自动分配所需数量，"
        f"请检查库存，或手动说明从哪些托盘规格各取多少箱。"
    )


def _loose_inbound_restatement_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    confirm_inbound_completion: a loose (box_count) original line has no
    default — nothing exists yet to default from, since inbound creates new
    storage rather than drawing from existing buckets. The warehouseman must
    state how the received loose boxes were packed: boxes_per_pallet +
    pallet_count, matching exactly what ApplyInboundStorageHandler writes —
    this must stay in sync with that handler's expected shape (a source/
    resulting conversion-pair shape was tried here previously and would have
    crashed with a raw KeyError even after "restatement", since the handler
    never looked for those field names at all).
    """
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

    restated_lines = collected_fields.get("received_lines") or []
    by_sku = {l["sku_code"]: l for l in restated_lines}
    missing = [
        sku for sku in loose_skus
        if not ({"boxes_per_pallet", "pallet_count"} <= set((by_sku.get(sku) or {}).keys()))
    ]
    if not missing:
        return None

    sku_labels = sku_label_map(db)
    missing_labels = "、".join(sku_labels.get(s, s) for s in sorted(missing))
    return f"商品 {missing_labels} 是散箱入库，请说明打包成了多少箱/托、共多少托。"


def _valid_destination_address_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    uchoice_outbound_request: destination_address_id must be a real
    uchoice_address row — the AI is instructed to only fill it in from the
    injected address candidate list (a fuzzy match against real UUIDs), but
    when nothing in that list matches what the customer described, it has
    been observed live to fall back to writing the customer's free-text
    company/address description into destination_address_id instead of
    leaving it unset. That string then reaches a raw
    `WHERE address_id = <value>::UUID` query downstream and crashes with a
    DB-level type error, not a clean message — this is the same class of bug
    as the loose-line/pallet-bucket issues fixed earlier: an AI field
    extraction that's supposed to be constrained to a known set of values
    needs a deterministic backstop, not just a prompt instruction.
    """
    dest_id = collected_fields.get("destination_address_id")
    if not dest_id:
        return None

    not_found_message = (
        "未能识别这个送货地址——它还没有被收录在地址库中，请提供完整的公司名、地址"
        "（门牌号+街道+城市+州+邮编）以及计费类型，联系管理员添加后再重新提交。"
    )

    import uuid
    try:
        uuid.UUID(str(dest_id))
    except (ValueError, TypeError):
        # Not even UUID-shaped — never send this to the DB. A raw
        # `::UUID` cast on a non-UUID string fails at the driver level and
        # leaves the SQLAlchemy session in a failed-transaction state for
        # the rest of this request, which is worse than the original crash.
        return not_found_message

    from models.uchoice import UchoiceAddress
    addr = db.query(UchoiceAddress).filter_by(address_id=dest_id).first()
    return None if addr is not None else not_found_message


PRE_CONFIRM_VALIDATORS = {
    "role_change": _last_admin_protection,
    "uchoice_outbound_request": _valid_destination_address_required,
    "confirm_outbound_completion": _loose_outbound_pick_required,
    "confirm_inbound_completion": _loose_inbound_restatement_required,
}


def run(service_type_name: str, context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """Returns an error message if the request should be blocked, else None."""
    validator = PRE_CONFIRM_VALIDATORS.get(service_type_name)
    if validator is None:
        return None
    return validator(context, collected_fields, db)
