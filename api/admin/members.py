from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.group import GroupConfig, GroupMember
from api.schemas import MemberCreate, MemberUpdate, MemberResponse

router = APIRouter(prefix="/admin/groups", dependencies=[Depends(verify_admin_key)])

VALID_ROLES = {"admin", "customer"}


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: str, body: MemberCreate, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'customer'")

    existing = db.query(GroupMember).filter_by(
        wechat_openid=body.wechat_openid, group_id=group_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="User already in this group")

    member = GroupMember(
        wechat_openid=body.wechat_openid,
        group_id=group_id,
        role=body.role,
        display_name=body.display_name,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return {"data": MemberResponse.model_validate(member)}


@router.get("/{group_id}/members")
def list_members(group_id: str, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    members = db.query(GroupMember).filter_by(group_id=group_id).all()
    return {"data": [MemberResponse.model_validate(m) for m in members]}


@router.patch("/{group_id}/members/{wechat_openid}")
def update_member(
    group_id: str, wechat_openid: str, body: MemberUpdate, db: Session = Depends(get_db)
):
    member = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid, group_id=group_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this group")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="role must be 'admin' or 'customer'")
        member.role = body.role
    if body.is_active is not None:
        member.is_active = body.is_active

    db.commit()
    db.refresh(member)
    return {"data": MemberResponse.model_validate(member)}


@router.delete("/{group_id}/members/{wechat_openid}")
def remove_member(group_id: str, wechat_openid: str, db: Session = Depends(get_db)):
    member = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid, group_id=group_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this group")

    db.delete(member)
    db.commit()
    return {"data": {"message": "member removed"}}
