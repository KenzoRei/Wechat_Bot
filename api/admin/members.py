from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.admin_invariants import lock_group_admin_invariant, would_remove_last_admin
from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES
from models.group import GroupConfig, GroupMember
from models.role import Role
from api.schemas import MemberCreate, MemberUpdate, MemberResponse

router = APIRouter(prefix="/admin/groups", dependencies=[Depends(verify_admin_key)])


def _resolve_assignable_role(db: Session, role_name: str) -> Role:
    """
    Restricted to ASSIGNABLE_ROLE_NAMES, matching api/admin/kefu_staff.py.
    "pending"
    is deliberately excluded here too: the pre-assignment state new
    self-registrations start in, not a target an admin assigns someone to.
    """
    if role_name not in ASSIGNABLE_ROLE_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"'{role_name}' is not an assignable role. Allowed: {sorted(ASSIGNABLE_ROLE_NAMES)}",
        )
    role = db.query(Role).filter_by(name=role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Unknown role: '{role_name}'. See GET /admin/roles")
    return role


def _clean_warehouse_code(raw: str | None) -> str | None:
    """Matches api/admin/kefu_staff.py's whitespace handling."""
    return (raw or "").strip() or None


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

    role = _resolve_assignable_role(db, body.role)

    warehouse_code = _clean_warehouse_code(body.warehouse_code)
    if role.name == "warehouseman" and not warehouse_code:
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
        warehouse_code=warehouse_code if role.name == "warehouseman" else None,
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

    # Must be acquired before counting and held through this request's own
    # commit below -- serializes this against any concurrent admin
    # mutation (Smart Bot or Kefu) targeting the same group's admin count.
    lock_group_admin_invariant(db, group_id)

    current_role = db.query(Role).filter_by(role_id=member.role_id).first()
    is_currently_active_admin = bool(
        current_role and current_role.name == "admin" and member.is_active
    )
    new_role = _resolve_assignable_role(db, body.role) if body.role is not None else None
    if would_remove_last_admin(
        db, group_id,
        is_currently_active_admin=is_currently_active_admin,
        new_role_name=new_role.name if new_role else None,
        new_is_active=body.is_active,
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot change this member's role/status — this group has only one active admin remaining.",
        )

    role_name = None
    if new_role is not None:
        role = new_role
        member.role_id = role.role_id
        role_name = role.name
        if role.name == "warehouseman":
            new_warehouse_code = body.warehouse_code if body.warehouse_code is not None else member.warehouse_code
            new_warehouse_code = _clean_warehouse_code(new_warehouse_code)
            if not new_warehouse_code:
                raise HTTPException(status_code=400, detail="warehouse_code is required for role=warehouseman")
            member.warehouse_code = new_warehouse_code
        else:
            # cleared automatically whenever a member's role changes away from warehouseman
            member.warehouse_code = None
    elif body.warehouse_code is not None:
        # role unchanged this call — only meaningful if the member is already a warehouseman
        if not current_role or current_role.name != "warehouseman":
            raise HTTPException(status_code=400, detail="warehouse_code only applies to role=warehouseman")
        cleaned = _clean_warehouse_code(body.warehouse_code)
        if not cleaned:
            raise HTTPException(status_code=400, detail="warehouse_code cannot be blank")
        member.warehouse_code = cleaned

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

    lock_group_admin_invariant(db, group_id)

    current_role = db.query(Role).filter_by(role_id=member.role_id).first()
    is_currently_active_admin = bool(
        current_role and current_role.name == "admin" and member.is_active
    )
    # Deletion is a stronger version of deactivation for this invariant's
    # purposes -- the member becomes not-an-active-admin either way.
    if would_remove_last_admin(
        db, group_id,
        is_currently_active_admin=is_currently_active_admin,
        new_role_name=None,
        new_is_active=False,
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot remove this member — this group has only one active admin remaining.",
        )

    db.delete(member)
    db.commit()
    return {"data": {"message": "member removed"}}
