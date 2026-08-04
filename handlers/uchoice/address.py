from handlers.base import BaseHandler


class UpsertAddressHandler(BaseHandler):
    """
    upsert_address — create-vs-update resolved by matched_address_id, which
    the AI sets in extracted_fields if it matched the customer's description
    against the injected address candidate list. The confirmation template
    (core/confirmation.py's _address_sections_builder) already surfaced which
    mode this is before the user confirmed.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.uchoice import UchoiceAddress

        fields = context.get("collected_fields", {})
        matched_id = fields.get("matched_address_id")
        created_by = context.get("wechat_openid")

        if matched_id:
            addr = db.query(UchoiceAddress).filter_by(address_id=matched_id).first()
            if addr is None:
                raise RuntimeError("待更新的地址不存在。")
            addr.company_name   = fields.get("company_name", addr.company_name)
            addr.charge_type    = fields.get("charge_type", addr.charge_type)
            addr.addr           = fields.get("addr", addr.addr)
            addr.warehouse_code = fields.get("warehouse_code", addr.warehouse_code)
            addr.note           = fields.get("note", addr.note)
            db.commit()
            return {"address_id": str(addr.address_id), "mode": "更新"}

        addr = UchoiceAddress(
            company_name=fields.get("company_name"),
            charge_type=fields.get("charge_type"),
            addr=fields.get("addr"),
            warehouse_code=fields.get("warehouse_code"),
            note=fields.get("note"),
            created_by=created_by,
        )
        db.add(addr)
        db.commit()
        db.refresh(addr)
        return {"address_id": str(addr.address_id), "mode": "新增"}
