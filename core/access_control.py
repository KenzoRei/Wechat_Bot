from dataclasses import dataclass
from uuid import UUID
from sqlalchemy.orm import Session as DBSession
from models.group import GroupConfig, GroupMember, GroupService
from models.service import ServiceType
from models.role import Role, RoleServicePermission

# Services whose turn machinery exists only in the Kefu pipeline.
KEFU_ONLY_SERVICE_NAMES = frozenset({
    "confirm_inbound_completion_batch",
    "confirm_outbound_completion_batch",
})


@dataclass
class AccessResult:
    wechat_openid:     str | None
    group_id:          UUID
    role:              str            # role name, e.g. "admin" — resolved from role_id
    role_id:           UUID
    display_name:      str | None
    warehouse_codes:   list[str] | None  # set only for role=warehouseman
    billing_customer_id: str | None      # set only for core.role_registry.CUSTOMER_IDENTITY_ROLE_NAMES roles -- see core.customer_directory.resolve_billing_customer_id
    allowed_services:  list[dict]
    group_context:     dict | None    # location presets, aliases — passed to AI
    group_description: str | None     # human label for the group — used in keHuDanHao
    # Defaults preserve Smart Robot's shape; only check_kefu_access sets these.
    source_channel:    str = "smart_robot"
    staff_id:          UUID | None = None


@dataclass
class AccessDenied:
    reason:      str
    notify_user: bool
    message:     str


def check_access(
    db: DBSession,
    wechat_openid: str,
    wechat_group_id: str
) -> AccessResult | AccessDenied:
    """
    Runs three checks in order:
    1. Group exists and is active   → silent ignore if not
    2. User is member and active    → notify user if not
    3. Load allowed services, filtered by role (deny-by-default — see below)
    """
    # 1. group check
    group = db.query(GroupConfig).filter_by(
        wechat_group_id=wechat_group_id,
        is_active=True
    ).first()

    if group is None:
        return AccessDenied(reason="group_not_found_or_inactive", notify_user=False, message="")

    # 2. member check (joined with role for the role name)
    row = (
        db.query(GroupMember, Role)
        .join(Role, GroupMember.role_id == Role.role_id)
        .filter(
            GroupMember.wechat_openid == wechat_openid,
            GroupMember.group_id == group.group_id,
        )
        .first()
    )

    if row is None:
        return AccessDenied(
            reason="user_not_member",
            notify_user=True,
            message=(
                "抱歉，您没有权限使用此服务。\n"
                f"您的用户ID：{wechat_openid}\n"
                "请将此ID提供给管理员以添加权限。"
            )
        )

    member, role = row

    if not member.is_active:
        return AccessDenied(
            reason="user_suspended",
            notify_user=True,
            message="您的账号已被暂停，请联系管理员。"
        )

    # 3. load allowed services — DENY BY DEFAULT.
    # A service_type_id is only included if BOTH: the group has it enabled
    # at all (GroupService -- real multi-tenancy: different tenants can
    # have different services enabled) AND this member's role has been
    # globally granted it (RoleServicePermission -- a role's own
    # capability, consistent across every tenant; see that model's
    # docstring for why this is no longer per-group).
    rows = (
        db.query(GroupService, ServiceType)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
        .join(RoleServicePermission, (
            (RoleServicePermission.service_type_id == GroupService.service_type_id) &
            (RoleServicePermission.role_id == member.role_id)
        ))
        .filter(
            GroupService.group_id == group.group_id,
            ServiceType.is_active == True
        )
        .all()
    )

    allowed_services = [
        {
            "service_type_id": str(gs.service_type_id),
            "name":            st.name,
            "description":     st.description,
            "keywords":        st.keywords or [],
            "workflow_id":     str(gs.workflow_id),
            "input_schema":    st.input_schema,   # tells AI which fields to collect
            "group_config":    gs.config,          # API credentials — stripped before sending to AI
            "requires_confirmation":    st.requires_confirmation,
            "targets_existing_request": st.targets_existing_request,
            "awaits_completion":        st.awaits_completion,
        }
        for gs, st in rows
        # Kefu-only services (batch completion -- its selection/execution
        # machinery lives only in core/kefu_turn_apply.py). Grants are
        # global, so they'd otherwise reach Smart Robot's AI too.
        if st.name not in KEFU_ONLY_SERVICE_NAMES
    ]

    return AccessResult(
        wechat_openid=wechat_openid,
        group_id=group.group_id,
        role=role.name,
        role_id=member.role_id,
        display_name=member.display_name,
        warehouse_codes=member.warehouse_codes,
        billing_customer_id=member.billing_customer_id,
        allowed_services=allowed_services,
        group_context=group.context,
        group_description=group.description,
    )


def check_kefu_access(
    db: DBSession,
    open_kfid: str,
    external_userid: str,
) -> AccessResult | AccessDenied:
    """
    Kefu-side equivalent of check_access(), reached through kefu_staff rather
    than GroupMember.
    (open_kfid, external_userid) -> kefu_staff row -> kefu_staff.group_id
    + role_id -> the same GroupService/RoleServicePermission tables Smart
    Robot already uses -- no separate grant mechanism, same deny-by-default.
    """
    from models.kefu import KefuStaff

    row = (
        db.query(KefuStaff, Role)
        .join(Role, KefuStaff.role_id == Role.role_id)
        .filter(
            KefuStaff.open_kfid == open_kfid,
            KefuStaff.external_userid == external_userid,
        )
        .first()
    )

    if row is None:
        return AccessDenied(
            reason="staff_not_registered",
            notify_user=True,
            message=(
                "您尚未注册为员工账号。\n"
                "请发送“注册成员”完成注册，注册后需管理员分配角色。"
            ),
        )

    staff, role = row

    if not staff.is_active:
        return AccessDenied(
            reason="staff_suspended",
            notify_user=True,
            message="您的账号已被暂停，请联系管理员。",
        )

    group = db.query(GroupConfig).filter_by(group_id=staff.group_id, is_active=True).first()
    if group is None:
        return AccessDenied(reason="group_not_found_or_inactive", notify_user=False, message="")

    rows = (
        db.query(GroupService, ServiceType)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
        .join(RoleServicePermission, (
            (RoleServicePermission.service_type_id == GroupService.service_type_id) &
            (RoleServicePermission.role_id == staff.role_id)
        ))
        .filter(
            GroupService.group_id == staff.group_id,
            ServiceType.is_active == True
        )
        .all()
    )

    allowed_services = [
        {
            "service_type_id": str(gs.service_type_id),
            "name":            st.name,
            "description":     st.description,
            "keywords":        st.keywords or [],
            "workflow_id":     str(gs.workflow_id),
            "input_schema":    st.input_schema,
            "group_config":    gs.config,
            "requires_confirmation":    st.requires_confirmation,
            "targets_existing_request": st.targets_existing_request,
            "awaits_completion":        st.awaits_completion,
        }
        for gs, st in rows
    ]

    return AccessResult(
        wechat_openid=None,
        group_id=staff.group_id,
        role=role.name,
        role_id=staff.role_id,
        display_name=staff.display_name,
        warehouse_codes=staff.warehouse_codes,
        billing_customer_id=staff.billing_customer_id,  # set only for CUSTOMER_IDENTITY_ROLE_NAMES roles (V31)
        allowed_services=allowed_services,
        group_context=group.context,
        group_description=group.description,
        source_channel="kefu",
        staff_id=staff.staff_id,
    )
