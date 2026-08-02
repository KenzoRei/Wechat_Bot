from dataclasses import dataclass
from uuid import UUID
from sqlalchemy.orm import Session as DBSession
from models.group import GroupConfig, GroupMember, GroupService, GroupServiceRole
from models.service import ServiceType
from models.role import Role


@dataclass
class AccessResult:
    wechat_openid:     str
    group_id:          UUID
    role:              str            # role name, e.g. "admin" — resolved from role_id
    role_id:           UUID
    display_name:      str | None
    allowed_services:  list[dict]
    group_context:     dict | None    # location presets, aliases — passed to AI
    group_description: str | None     # human label for the group — used in keHuDanHao


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
            message="抱歉，您没有权限使用此服务。"
        )

    member, role = row

    if not member.is_active:
        return AccessDenied(
            reason="user_suspended",
            notify_user=True,
            message="您的账号已被暂停，请联系管理员。"
        )

    # 3. load allowed services — DENY BY DEFAULT.
    # A (group_id, service_type_id) is only included if a matching row exists
    # in group_service_role for this member's role_id.
    rows = (
        db.query(GroupService, ServiceType)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
        .join(GroupServiceRole, (
            (GroupServiceRole.group_id == GroupService.group_id) &
            (GroupServiceRole.service_type_id == GroupService.service_type_id) &
            (GroupServiceRole.role_id == member.role_id)
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
            "workflow_id":     str(gs.workflow_id),
            "input_schema":    st.input_schema,   # tells AI which fields to collect
            "group_config":    gs.config,          # API credentials — stripped before sending to AI
        }
        for gs, st in rows
    ]

    return AccessResult(
        wechat_openid=wechat_openid,
        group_id=group.group_id,
        role=role.name,
        role_id=member.role_id,
        display_name=member.display_name,
        allowed_services=allowed_services,
        group_context=group.context,
        group_description=group.description,
    )
