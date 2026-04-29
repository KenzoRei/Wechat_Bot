from dataclasses import dataclass
from uuid import UUID
from sqlalchemy.orm import Session as DBSession
from models.group import GroupConfig, GroupMember, GroupService
from models.service import ServiceType


@dataclass
class AccessResult:
    wechat_openid:    str
    group_id:         UUID
    role:             str
    display_name:     str | None
    allowed_services: list[dict]


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
    3. Load allowed services        → included in AccessResult
    """
    # 1. group check
    group = db.query(GroupConfig).filter_by(
        wechat_group_id=wechat_group_id,
        is_active=True
    ).first()

    if group is None:
        return AccessDenied(reason="group_not_found_or_inactive", notify_user=False, message="")

    # 2. member check
    member = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid,
        group_id=group.group_id
    ).first()

    if member is None:
        return AccessDenied(
            reason="user_not_member",
            notify_user=True,
            message="抱歉，您没有权限使用此服务。"
        )

    if not member.is_active:
        return AccessDenied(
            reason="user_suspended",
            notify_user=True,
            message="您的账号已被暂停，请联系管理员。"
        )

    # 3. load allowed services (includes group-specific config)
    rows = (
        db.query(GroupService, ServiceType)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
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
            "group_config":    gs.config,
        }
        for gs, st in rows
    ]

    return AccessResult(
        wechat_openid=wechat_openid,
        group_id=group.group_id,
        role=member.role,
        display_name=member.display_name,
        allowed_services=allowed_services,
    )
