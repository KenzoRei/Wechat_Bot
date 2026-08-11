from handlers.base import BaseHandler


class RoleChangeHandler(BaseHandler):
    """
    role_change — the pre-confirm validators already ran before this ever
    got to a confirmation template, but per phase4-self-registration.md
    Sec 4 boundary 3 (Codex round-37/round-41) this repeats the
    authoritative checks immediately before mutation, so the handler fails
    safe even if invoked outside the normal confirm-turn path.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.group import GroupMember
        from models.role import Role
        from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES, VALID_WAREHOUSE_CODES

        fields = context.get("collected_fields", {})
        target_openid = fields.get("target_openid")
        new_role_name = fields.get("new_role")
        warehouse_code = fields.get("warehouse_code")
        group_id = context.get("group_id")

        member = db.query(GroupMember).filter_by(wechat_openid=target_openid, group_id=group_id).first()
        if member is None:
            raise RuntimeError("目标成员不在本群组中。")

        if new_role_name not in ASSIGNABLE_ROLE_NAMES:
            raise RuntimeError(f"未知角色：{new_role_name}")

        if new_role_name == "warehouseman" and (not warehouse_code or warehouse_code not in VALID_WAREHOUSE_CODES):
            raise RuntimeError("指派为仓库管理员需要提供有效的仓库代码（JFK 或 DE）。")

        role = db.query(Role).filter_by(name=new_role_name).first()
        if role is None:
            raise RuntimeError(f"未知角色：{new_role_name}")

        member.role_id = role.role_id
        # warehouse_code is meaningful only for warehouseman — cleared on any other role
        member.warehouse_code = warehouse_code if new_role_name == "warehouseman" else None
        db.commit()

        return {"target_openid": target_openid, "new_role": new_role_name}
