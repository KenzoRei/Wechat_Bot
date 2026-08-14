from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.admin_invariants import lock_group_admin_invariant, would_remove_last_admin
from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES
from models.kefu import KefuStaff
from models.role import Role
from api.schemas import KefuStaffUpdate, KefuStaffResponse

router = APIRouter(prefix="/admin/kefu-staff", dependencies=[Depends(verify_admin_key)])


def _resolve_assignable_role(db: Session, role_name: str) -> Role:
    """
    Only ASSIGNABLE_ROLE_NAMES are reachable through this endpoint -- same
    allowlist the conversational role_change service enforces
    (core/uchoice_field_sanitization.py's _sanitize_role_change_fields_before_persistence).
    "pending" is deliberately excluded: it's the pre-assignment state new
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


def _to_response(staff: KefuStaff, role_name: str) -> KefuStaffResponse:
    return KefuStaffResponse(
        staff_id=staff.staff_id,
        open_kfid=staff.open_kfid,
        external_userid=staff.external_userid,
        group_id=staff.group_id,
        role=role_name,
        display_name=staff.display_name,
        warehouse_code=staff.warehouse_code,
        is_active=staff.is_active,
        created_at=staff.created_at,
    )


@router.get("")
def list_kefu_staff(pending_only: bool = Query(False), db: Session = Depends(get_db)):
    query = db.query(KefuStaff, Role).join(Role, KefuStaff.role_id == Role.role_id)
    if pending_only:
        query = query.filter(Role.name == "pending")
    rows = query.order_by(KefuStaff.created_at.desc()).all()
    return {"data": [_to_response(s, r.name) for s, r in rows]}


@router.patch("/{staff_id}")
def update_kefu_staff(staff_id: str, body: KefuStaffUpdate, db: Session = Depends(get_db)):
    staff = db.query(KefuStaff).filter_by(staff_id=staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Kefu staff member not found")

    # Must be acquired before counting and held through this request's own
    # commit below -- serializes this against any concurrent admin
    # mutation (Smart Bot or Kefu) targeting the same group's admin count.
    lock_group_admin_invariant(db, staff.group_id)

    current_role = db.query(Role).filter_by(role_id=staff.role_id).first()
    is_currently_active_admin = bool(
        current_role and current_role.name == "admin" and staff.is_active
    )

    new_role = _resolve_assignable_role(db, body.role) if body.role is not None else None
    if would_remove_last_admin(
        db, staff.group_id,
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
        staff.role_id = role.role_id
        role_name = role.name
        if role.name == "warehouseman":
            new_warehouse_code = body.warehouse_code if body.warehouse_code is not None else staff.warehouse_code
            new_warehouse_code = (new_warehouse_code or "").strip() or None
            if not new_warehouse_code:
                raise HTTPException(status_code=400, detail="warehouse_code is required for role=warehouseman")
            staff.warehouse_code = new_warehouse_code
        else:
            # cleared automatically whenever a staff member's role changes away from warehouseman
            staff.warehouse_code = None
    elif body.warehouse_code is not None:
        # role unchanged this call — only meaningful if the staff member is already a warehouseman
        if not current_role or current_role.name != "warehouseman":
            raise HTTPException(status_code=400, detail="warehouse_code only applies to role=warehouseman")
        stripped = body.warehouse_code.strip()
        if not stripped:
            raise HTTPException(status_code=400, detail="warehouse_code cannot be blank")
        staff.warehouse_code = stripped

    if body.display_name is not None:
        staff.display_name = body.display_name

    if body.is_active is not None:
        staff.is_active = body.is_active

    db.commit()
    db.refresh(staff)

    if role_name is None:
        role_name = db.query(Role).filter_by(role_id=staff.role_id).first().name

    return {"data": _to_response(staff, role_name)}
