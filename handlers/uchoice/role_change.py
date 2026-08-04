from handlers.base import BaseHandler


class RoleChangeHandler(BaseHandler):
    """
    role_change — the last-admin-protection check already ran in
    core/pre_confirm_validators.py before this ever got to a confirmation
    template, so by execution time the change is known-safe to apply.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.group import GroupMember
        from models.role import Role

        fields = context.get("collected_fields", {})
        target_openid = fields.get("target_openid")
        new_role_name = fields.get("new_role")
        warehouse_code = fields.get("warehouse_code")
        group_id = context.get("group_id")

        member = db.query(GroupMember).filter_by(wechat_openid=target_openid, group_id=group_id).first()
        if member is None:
            raise RuntimeError("目标成员不在本群组中。")

        role = db.query(Role).filter_by(name=new_role_name).first()
        if role is None:
            raise RuntimeError(f"未知角色：{new_role_name}")

        member.role_id = role.role_id
        # warehouse_code is meaningful only for warehouseman — cleared on any other role
        member.warehouse_code = warehouse_code if new_role_name == "warehouseman" else None
        db.commit()

        return {"target_openid": target_openid, "new_role": new_role_name}
