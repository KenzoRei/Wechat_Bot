from handlers.base import BaseHandler


def _clean(value):
    """Trim contact/phone before storing (the length validator checks the trimmed value); blank -> None."""
    return value.strip() or None if isinstance(value, str) else value


class AddAddressHandler(BaseHandler):
    """
    add_address -- creates ONE new address in the shared U-Choice address
    book. Create-only: editing an existing address is the separate
    update_address service (its own permission), so a leftover
    matched_address_id (e.g. an edit draft from before the split) is
    rejected here rather than silently creating a second address.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.uchoice import UchoiceAddress
        from core import address_dedupe, role_policy

        fields = context.get("collected_fields", {})
        if fields.get("matched_address_id"):
            raise RuntimeError(
                "新增地址不支持修改已有地址。请重新发起：新增请直接提供新地址；修改已有地址请使用「更新地址」。"
            )
        # customer_id is authoritative context: the case's locked customer,
        # never a model-generated company_name string -- present only for
        # Kefu-originated cases, per the case-turn service. Smart Robot's
        # existing pivot flow (core/workflow_engine.py
        # _maybe_pivot_to_add_address) has no customer_id concept and is
        # unaffected: an address created without one simply stays
        # unassigned, same as the 5 pre-existing null-company rows, not a
        # new failure mode.
        customer_id = context.get("customer_id")
        role_name = context.get("role")
        allowed = context.get("warehouse_codes")

        # wechat_openid is always None for Kefu-originated turns (Kefu has
        # no such identity at all -- see core/kefu_turn_apply.py) -- must
        # fall back to submitted_by_staff_id, same as
        # handlers/uchoice/storage_txns.py's _actor_id. Without this,
        # every Kefu-originated new address crashes on uchoice_address's
        # NOT NULL created_by constraint (observed live, msgid
        # B7AMS7ixMesqF5r4f4DWZ6DAa3).
        created_by = context.get("wechat_openid") or context.get("submitted_by_staff_id")
        if not created_by:
            raise RuntimeError("无法确定操作人身份，无法新增地址。")

        # Matches core.pre_confirm_validators._valid_add_address_
        # warehouse_scope's final check -- the execution-time backstop
        # (2026-09-15 audit finding 2).
        message = role_policy.check_warehouse_scope(role_name, allowed, fields.get("warehouse_code"))
        if message:
            raise RuntimeError(message)

        # Authoritative duplicate guard. The pre-confirm check can be raced
        # (two sessions, or a row created after the card was shown), so
        # serialise writers of the same key and re-check inside the
        # transaction before inserting.
        key = address_dedupe.duplicate_key(
            customer_id, fields.get("addr"), fields.get("warehouse_code"), fields.get("charge_type")
        )
        address_dedupe.lock_for_key(db, key)
        existing = address_dedupe.find_duplicate(
            db, customer_id, fields.get("addr"), fields.get("warehouse_code"), fields.get("charge_type")
        )
        if existing is not None:
            raise RuntimeError(address_dedupe.duplicate_message(existing))

        addr = UchoiceAddress(
            company_name=fields.get("company_name"),
            charge_type=fields.get("charge_type"),
            addr=fields.get("addr"),
            warehouse_code=fields.get("warehouse_code"),
            note=fields.get("note"),
            contact=_clean(fields.get("contact")),
            phone=_clean(fields.get("phone")),
            created_by=created_by,
            customer_id=customer_id,
        )
        db.add(addr)
        if config.get("_defer_commit", False):
            db.flush()
        else:
            db.commit()
            db.refresh(addr)
        return {"address_id": str(addr.address_id), "mode": "新增"}
