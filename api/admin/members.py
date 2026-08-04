from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.group import GroupConfig, GroupMember
from models.role import Role
from api.schemas import MemberCreate, MemberUpdate, MemberResponse

router = APIRouter(prefix="/admin/groups", dependencies=[Depends(verify_admin_key)])


def _resolve_role(db: Session, role_name: str) -> Role:
    role = db.query(Role).filter_by(name=role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Unknown role: '{role_name}'. See GET /admin/roles")
    return role


def _to_response(member: GroupMember, role_name: str) -> MemberResponse:
    return MemberResponse(
        wechat_openid=member.wechat_openid,
        group_id=member.group_id,
        role=role_name,
        display_name=member.display_name,
        warehouse_code=member.warehouse_code,
        is_active=member.is_active,
        joined_at=member.joined_at,
    )


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: str, body: MemberCreate, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    role = _resolve_role(db, body.role)

    if role.name == "warehouseman" and not body.warehouse_code:
        raise HTTPException(status_code=400, detail="warehouse_code is required for role=warehouseman")

    existing = db.query(GroupMember).filter_by(
        wechat_openid=body.wechat_openid, group_id=group_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="User already in this group")

    member = GroupMember(
        wechat_openid=body.wechat_openid,
        group_id=group_id,
        role_id=role.role_id,
        display_name=body.display_name,
        warehouse_code=body.warehouse_code if role.name == "warehouseman" else None,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return {"data": _to_response(member, role.name)}


@router.get("/{group_id}/members")
def list_members(group_id: str, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rows = (
        db.query(GroupMember, Role)
        .join(Role, GroupMember.role_id == Role.role_id)
        .filter(GroupMember.group_id == group_id)
        .all()
    )
    return {"data": [_to_response(m, r.name) for m, r in rows]}


@router.patch("/{group_id}/members/{wechat_openid}")
def update_member(
    group_id: str, wechat_openid: str, body: MemberUpdate, db: Session = Depends(get_db)
):
    member = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid, group_id=group_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this group")

    role_name = None
    if body.role is not None:
        role = _resolve_role(db, body.role)
        member.role_id = role.role_id
        role_name = role.name
        if role.name == "warehouseman":
            new_warehouse_code = body.warehouse_code if body.warehouse_code is not None else member.warehouse_code
            if not new_warehouse_code:
                raise HTTPException(status_code=400, detail="warehouse_code is required for role=warehouseman")
            member.warehouse_code = new_warehouse_code
        else:
            # cleared automatically whenever a member's role changes away from warehouseman
            member.warehouse_code = None
    elif body.warehouse_code is not None:
        # role unchanged this call — only meaningful if the member is already a warehouseman
        current_role = db.query(Role).filter_by(role_id=member.role_id).first()
        if not current_role or current_role.name != "warehouseman":
            raise HTTPException(status_code=400, detail="warehouse_code only applies to role=warehouseman")
        member.warehouse_code = body.warehouse_code

    if body.is_active is not None:
        member.is_active = body.is_active

    db.commit()
    db.refresh(member)

    if role_name is None:
        role_name = db.query(Role).filter_by(role_id=member.role_id).first().name

    return {"data": _to_response(member, role_name)}


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
