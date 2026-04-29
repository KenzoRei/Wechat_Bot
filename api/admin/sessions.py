from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.session import ConversationSession
from models.group import GroupMember
from models.service import ServiceType
from api.schemas import SessionResponse

router = APIRouter(prefix="/admin/sessions", dependencies=[Depends(verify_admin_key)])


@router.get("")
def list_active_sessions(db: Session = Depends(get_db)):
    rows = (
        db.query(ConversationSession, GroupMember.display_name, ServiceType.name.label("service_name"))
        .outerjoin(GroupMember, and_(
            GroupMember.wechat_openid == ConversationSession.wechat_openid,
            GroupMember.group_id == ConversationSession.group_id
        ))
        .outerjoin(ServiceType, ServiceType.service_type_id == ConversationSession.service_type_id)
        .filter(ConversationSession.status.in_(["active", "pending_confirmation"]))
        .order_by(ConversationSession.created_at.desc())
        .all()
    )

    return {"data": [
        SessionResponse(
            session_id=session.session_id,
            wechat_openid=session.wechat_openid,
            display_name=display_name,
            group_id=session.group_id,
            service_name=service_name,
            status=session.status,
            collected_fields=session.collected_fields,
            expires_at=session.expires_at,
            created_at=session.created_at,
        )
        for session, display_name, service_name in rows
    ]}
