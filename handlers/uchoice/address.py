from core.workflow_errors import AddressOperationRejected
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
            raise AddressOperationRejected(
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
            raise AddressOperationRejected("无法确定操作人身份，无法新增地址。")

        # Matches core.pre_confirm_validators._valid_add_address_
        # warehouse_scope's final check -- the execution-time backstop
        # (2026-09-15 audit finding 2).
        message = role_policy.check_warehouse_scope(role_name, allowed, fields.get("warehouse_code"))
        if message:
            raise AddressOperationRejected(message)

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
            raise AddressOperationRejected(address_dedupe.duplicate_message(existing))

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


class UpdateAddressHandler(BaseHandler):
    """
    update_address -- edits ONE existing address chosen by matched_address_id.

    Everything is re-checked here against freshly loaded, row-locked state
    (the pre-confirm validators ran earlier and the grant may have been
    revoked, or the row changed, since): target exists, warehouse scope on
    the existing AND the requested warehouse, admin-only effective changes,
    baseline (stale) check, and the duplicate key of the resulting row.
    Only EDITABLE_FIELDS are ever written; customer_id is never touched.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core import address_dedupe, address_update, role_policy

        fields = context.get("collected_fields", {})
        role_name = context.get("role")
        allowed = context.get("warehouse_codes")

        if "customer_id" in fields:
            raise AddressOperationRejected("地址所属客户不可修改。")
        row = address_update.load_target(db, fields, lock=True)
        if row is None:
            raise AddressOperationRejected("待更新的地址不存在。")
        blank = address_update.blank_required_field(fields) or address_update.validate_values(fields)
        if blank:
            raise AddressOperationRejected(blank)
        if address_update.stale_against_baseline(row, fields):
            raise AddressOperationRejected(address_update.STALE_MESSAGE)

        changes = address_update.effective_changes(address_update.snapshot(row), fields)
        if not changes:
            raise AddressOperationRejected("没有检测到任何需要修改的内容。")
        if set(changes) & set(address_update.ADMIN_ONLY_FIELDS) and role_name != "admin":
            raise AddressOperationRejected("只有管理员可以修改计费类型或所属仓库。")

        after = address_update.post_update_values(row, changes)
        message = (
            role_policy.check_warehouse_scope(role_name, allowed, row.warehouse_code)
            or role_policy.check_warehouse_scope(role_name, allowed, after["warehouse_code"])
        )
        if message:
            raise AddressOperationRejected(message)

        key = address_dedupe.duplicate_key(row.customer_id, after["addr"], after["warehouse_code"], after["charge_type"])
        address_dedupe.lock_for_key(db, key)
        duplicate = address_dedupe.find_duplicate(
            db, row.customer_id, after["addr"], after["warehouse_code"], after["charge_type"], exclude_id=row.address_id
        )
        if duplicate is not None:
            raise AddressOperationRejected(address_dedupe.duplicate_message(duplicate))

        for field, (_old, new) in changes.items():
            setattr(row, field, new)
        if config.get("_defer_commit", False):
            db.flush()
        else:
            db.commit()
        return {
            "address_id": str(row.address_id),
            "mode": "更新",
            "changes": {field: [old, new] for field, (old, new) in changes.items()},
        }
