from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.group import GroupConfig
from api.schemas import GroupCreate, GroupUpdate, GroupResponse

router = APIRouter(prefix="/admin/groups", dependencies=[Depends(verify_admin_key)])


@router.post("", status_code=201)
def create_group(body: GroupCreate, db: Session = Depends(get_db)):
    existing = db.query(GroupConfig).filter_by(wechat_group_id=body.wechat_group_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="wechat_group_id already exists")

    group = GroupConfig(
        wechat_group_id=body.wechat_group_id,
        description=body.description,
        daily_request_limit=body.daily_request_limit,
        context=body.context,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"data": GroupResponse.model_validate(group)}


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    groups = db.query(GroupConfig).order_by(GroupConfig.created_at.desc()).all()
    return {"data": [GroupResponse.model_validate(g) for g in groups]}


@router.patch("/{group_id}")
def update_group(group_id: str, body: GroupUpdate, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if body.description is not None:
        group.description = body.description
    if body.is_active is not None:
        group.is_active = body.is_active
    if body.daily_request_limit is not None:
        group.daily_request_limit = body.daily_request_limit
    if "context" in body.model_fields_set:   # explicit null clears it; omitting leaves unchanged
        group.context = body.context

    db.commit()
    db.refresh(group)
    return {"data": GroupResponse.model_validate(group)}
