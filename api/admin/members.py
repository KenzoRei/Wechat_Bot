from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.admin_invariants import lock_group_admin_invariant, would_remove_last_admin
from core.role_registry import ASSIGNABLE_ROLE_NAMES
from core import role_policy
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


def _assignment_fields_for_role(db: Session, role_name: str, warehouse_codes: list[str] | None, billing_customer_id: str | None) -> dict:
    """
    Adapter over core.role_policy.normalize_assignment_fields (the one
    shared assignment-validation entry point, ADR-010 step 1): this is a
    direct admin write (not AI-extracted input needing a "preserve valid
    progress" fallback), so every rejection is a 400 with the same
    messages this endpoint has always returned.
    """
    try:
        return role_policy.normalize_assignment_fields(db, role_name, {
            "warehouse_codes": warehouse_codes,
            "billing_customer_id": billing_customer_id,
        })
    except role_policy.RoleAssignmentError as exc:
        if exc.field == "warehouse_codes":
            if exc.reason == "missing":
                raise HTTPException(status_code=400, detail=f"warehouse_codes is required for role={role_name}")
            raise HTTPException(status_code=400, detail=f"Unknown warehouse code(s): {exc.info['unknown']}")
        if exc.reason == "missing":
            raise HTTPException(status_code=400, detail="billing_customer_id is required for a customer-identity role")
        if exc.reason == "not_found":
            raise HTTPException(status_code=400, detail=f"Unknown customer_id: '{exc.info['customer_id']}'. See GET /admin/customers")
        raise HTTPException(status_code=400, detail=f"Customer '{exc.info['customer_id']}' is not active (status={exc.info['status']!r})")


def _to_response(member: GroupMember, role_name: str) -> MemberResponse:
    return MemberResponse(
        wechat_openid=member.wechat_openid,
        group_id=member.group_id,
        role=role_name,
        display_name=member.display_name,
        warehouse_codes=member.warehouse_codes,
        billing_customer_id=member.billing_customer_id,
        is_active=member.is_active,
        joined_at=member.joined_at,
    )


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: str, body: MemberCreate, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    role = _resolve_assignable_role(db, body.role)

    normalized = _assignment_fields_for_role(db, role.name, body.warehouse_codes, body.billing_customer_id)

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
        warehouse_codes=normalized["warehouse_codes"],
        billing_customer_id=normalized["billing_customer_id"],
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
        new_warehouse_codes = body.warehouse_codes if body.warehouse_codes is not None else member.warehouse_codes
        new_billing_id = body.billing_customer_id if body.billing_customer_id is not None else member.billing_customer_id
        # Returns None (clearing the field) for whichever field role.name
        # doesn't require -- a former holder's stale assignment must never
        # survive a reassignment away from a scoped/identity role.
        normalized = _assignment_fields_for_role(db, role.name, new_warehouse_codes, new_billing_id)
        member.warehouse_codes = normalized["warehouse_codes"]
        member.billing_customer_id = normalized["billing_customer_id"]
    elif body.warehouse_codes is not None:
        # role unchanged this call — only meaningful if the member's current role is warehouse-scoped
        if not current_role or not role_policy.requires_warehouse_scope(current_role.name):
            raise HTTPException(status_code=400, detail=f"warehouse_codes only applies to a warehouse-scoped role, not '{current_role.name if current_role else None}'")
        normalized = _assignment_fields_for_role(db, current_role.name, body.warehouse_codes, member.billing_customer_id)
        member.warehouse_codes = normalized["warehouse_codes"]

    if new_role is None and body.billing_customer_id is not None:
        # role unchanged this call — only meaningful if the member already holds a customer-identity role
        if not current_role or not role_policy.requires_billing_customer(current_role.name):
            raise HTTPException(status_code=400, detail="billing_customer_id only applies to a customer-identity role")
        normalized = _assignment_fields_for_role(db, current_role.name, member.warehouse_codes, body.billing_customer_id)
        member.billing_customer_id = normalized["billing_customer_id"]

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
