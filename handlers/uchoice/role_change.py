from handlers.base import BaseHandler


class RoleChangeHandler(BaseHandler):
    """
    role_change — the pre-confirm validators already ran before this ever
    reached confirmation, but this repeats authoritative checks immediately
    before mutation so the handler fails
    safe even if invoked outside the normal confirm-turn path.

    target_openid may be a tagged `kefu:<staff_id>` identity
    (core/role_identity.py) or a bare
    Smart Robot wechat_openid -- dispatch is always by the explicit tag,
    never by probing which table happens to contain a matching raw string.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.group import GroupMember
        from models.role import Role
        from core.admin_invariants import lock_group_admin_invariant, would_remove_last_admin
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

        # _last_admin_protection runs before confirmation, but this mutation
        # boundary still
        # confirmation prompt -- unlocked, and with a real time gap (the
        # user has to actually confirm) during which a concurrent REST
        # admin-API call or another chat confirmation could change the
        # group's admin count. This is the actual mutation point, so this
        # is where the invariant must be authoritatively re-checked, under
        # the same advisory lock the REST APIs use, immediately before
        # commit -- not trusted from the earlier pre-confirm pass.
        lock_group_admin_invariant(db, group_id)
        db.refresh(target)  # re-read post-lock in case it changed underneath the earlier check

        current_role = db.query(Role).filter_by(role_id=target.role_id).first()
        is_currently_active_admin = bool(
            current_role and current_role.name == "admin" and target.is_active
        )
        if would_remove_last_admin(
            db, group_id,
            is_currently_active_admin=is_currently_active_admin,
            new_role_name=new_role_name,
            new_is_active=None,  # this handler never changes is_active
        ):
            raise RuntimeError("无法将该成员的角色改为非管理员——该群组当前仅剩一名管理员。")

        target.role_id = role.role_id
        # warehouse_code is meaningful only for warehouseman — cleared on any other role
        target.warehouse_code = warehouse_code if new_role_name == "warehouseman" else None
        db.commit()

        return {"target_openid": target_openid, "new_role": new_role_name}
