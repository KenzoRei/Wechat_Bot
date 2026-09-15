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
        from core.role_registry import ASSIGNABLE_ROLE_NAMES
        from core.uchoice_constants import VALID_WAREHOUSE_CODES
        from core.role_identity import parse_target_identity
        from core import role_policy

        fields = context.get("collected_fields", {})
        target_openid = fields.get("target_openid")
        new_role_name = fields.get("new_role")
        warehouse_codes = fields.get("warehouse_codes")
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

        # One shared entry point for both assignment-level fields (ADR-010
        # step 1) -- pre_confirm_validators.py's _valid_role_change_target_
        # and_role already rejects a missing/invalid value before
        # confirmation is ever shown; this is the execution-time backstop.
        try:
            normalized = role_policy.normalize_assignment_fields(db, new_role_name, {
                "warehouse_codes": warehouse_codes,
                "billing_customer_id": fields.get("billing_customer_id") or getattr(target, "billing_customer_id", None),
            })
        except role_policy.RoleAssignmentError as exc:
            if exc.field == "warehouse_codes":
                codes_list = "、".join(sorted(VALID_WAREHOUSE_CODES))
                raise RuntimeError(f"指派为{new_role_name}需要提供至少一个有效的仓库代码（{codes_list}）。")
            if exc.reason == "missing":
                raise RuntimeError("指派为客户角色需要提供关联的客户编号。")
            billing_id = exc.info.get("customer_id", "")
            raise RuntimeError(f"客户编号 {billing_id} 无效或未激活，无法关联，请联系管理员。")
        warehouse_codes_to_set = normalized["warehouse_codes"]
        billing_customer_id_to_set = normalized["billing_customer_id"]

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
        # warehouse_codes is meaningful only for a warehouse-scoped role
        # (core.role_registry.WAREHOUSE_SCOPED_ROLE_NAMES) -- cleared on any
        # other role, same as billing_customer_id below.
        target.warehouse_codes = warehouse_codes_to_set
        # billing_customer_id exists on both GroupMember and KefuStaff since
        # V31 -- meaningful only for CUSTOMER_IDENTITY_ROLE_NAMES roles,
        # cleared on any other role, same pattern as warehouse_codes above.
        # A former customer-identity holder's stale binding must never
        # survive a reassignment away from one of those roles.
        target.billing_customer_id = billing_customer_id_to_set
        db.commit()

        return {"target_openid": target_openid, "new_role": new_role_name}
