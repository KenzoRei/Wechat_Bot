from handlers.base import BaseHandler


class RoleChangeHandler(BaseHandler):
    """
    role_change — the pre-confirm validators already ran before this ever
    got to a confirmation template, but per phase4-self-registration.md
    Sec 4 boundary 3 (Codex round-37/round-41) this repeats the
    authoritative checks immediately before mutation, so the handler fails
    safe even if invoked outside the normal confirm-turn path.

    kefu-migration-plan.md Sec 2.3: target_openid may be a tagged
    "kefu:<staff_id>" identifier (core/role_identity.py) as well as a bare
    Smart Robot wechat_openid -- dispatch is always by the explicit tag,
    never by probing which table happens to contain a matching raw string.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.group import GroupMember
        from models.role import Role
        from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES, VALID_WAREHOUSE_CODES
        from core.role_identity import parse_target_identity

        fields = context.get("collected_fields", {})
        target_openid = fields.get("target_openid")
        new_role_name = fields.get("new_role")
        warehouse_code = fields.get("warehouse_code")
        group_id = context.get("group_id")

        identity = parse_target_identity(target_openid)
        if identity is None:
            raise RuntimeError("目标成员不在本群组中。")

        if identity.kind == "kefu":
            from models.kefu import KefuStaff
            target = db.query(KefuStaff).filter_by(staff_id=identity.key, group_id=group_id).first()
        else:
            target = db.query(GroupMember).filter_by(wechat_openid=identity.key, group_id=group_id).first()
        if target is None:
            raise RuntimeError("目标成员不在本群组中。")

        if new_role_name not in ASSIGNABLE_ROLE_NAMES:
            raise RuntimeError(f"未知角色：{new_role_name}")

        if new_role_name == "warehouseman" and (not warehouse_code or warehouse_code not in VALID_WAREHOUSE_CODES):
            raise RuntimeError("指派为仓库管理员需要提供有效的仓库代码（JFK 或 DE）。")

        role = db.query(Role).filter_by(name=new_role_name).first()
        if role is None:
            raise RuntimeError(f"未知角色：{new_role_name}")

        target.role_id = role.role_id
        # warehouse_code is meaningful only for warehouseman — cleared on any other role
        target.warehouse_code = warehouse_code if new_role_name == "warehouseman" else None
        db.commit()

        return {"target_openid": target_openid, "new_role": new_role_name}
