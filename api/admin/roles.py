from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.role_registry import ASSIGNABLE_ROLE_NAMES
from core.uchoice_constants import VALID_WAREHOUSE_CODES
from models.role import Role, RoleServicePermission
from models.service import ServiceType
from api.schemas import RoleCreate, RoleResponse, RoleServicePermissionGrant, RoleServicePermissionResponse

router = APIRouter(prefix="/admin/roles", dependencies=[Depends(verify_admin_key)])


def _to_response(role: Role) -> RoleResponse:
    return RoleResponse(
        role_id=role.role_id,
        name=role.name,
        description=role.description,
        created_at=role.created_at,
        assignable=role.name in ASSIGNABLE_ROLE_NAMES,
    )


@router.get("")
def list_roles(db: Session = Depends(get_db)):
    roles = db.query(Role).order_by(Role.name).all()
    # A separate top-level key, not folded into `data` -- keeps the
    # existing `resp.data` array-of-roles shape untouched for every
    # existing caller. Sourced from the same VALID_WAREHOUSE_CODES constant
    # the backend already validates warehouse_codes against, so the panel's
    # checkbox list can never silently drift from what the server actually
    # accepts.
    return {"data": [_to_response(r) for r in roles], "warehouse_codes": sorted(VALID_WAREHOUSE_CODES)}


@router.post("", status_code=201)
def create_role(body: RoleCreate, db: Session = Depends(get_db)):
    existing = db.query(Role).filter_by(name=body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name already exists")

    role = Role(name=body.name, description=body.description)
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"data": _to_response(role)}


# ── Role->service permission grants (deny-by-default, GLOBAL) ───────────────
# A role has zero access to a service unless a matching row exists here.
# Global, not per-group -- see models.role.RoleServicePermission's docstring
# for why (replaces the old, per-group group_service_role). Whether a given
# GROUP even has the service enabled at all is a separate, still-per-group
# concern (see api/admin/services.py's GroupService endpoints); a role's
# actual reachable services in a group are the intersection of both.

def _to_permission_response(grant: RoleServicePermission, role: Role, service: ServiceType) -> RoleServicePermissionResponse:
    return RoleServicePermissionResponse(
        role_id=grant.role_id,
        role=role.name,
        service_type_id=grant.service_type_id,
        service_name=service.name,
        created_by=grant.created_by,
        created_at=grant.created_at,
    )


@router.get("/{role_id}/services")
def list_role_service_permissions(role_id: str, db: Session = Depends(get_db)):
    role = db.query(Role).filter_by(role_id=role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    rows = (
        db.query(RoleServicePermission, ServiceType)
        .join(ServiceType, RoleServicePermission.service_type_id == ServiceType.service_type_id)
        .filter(RoleServicePermission.role_id == role_id)
        .all()
    )
    return {"data": [_to_permission_response(g, role, st) for g, st in rows]}


@router.post("/{role_id}/services/{service_type_id}", status_code=201)
def grant_role_service_permission(
    role_id: str, service_type_id: str, body: RoleServicePermissionGrant,
    db: Session = Depends(get_db),
):
    role = db.query(Role).filter_by(role_id=role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    service = db.query(ServiceType).filter_by(service_type_id=service_type_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="service_type_id not found")

    existing = db.query(RoleServicePermission).filter_by(role_id=role_id, service_type_id=service_type_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role already granted access to this service")

    grant = RoleServicePermission(role_id=role_id, service_type_id=service_type_id, created_by=body.created_by)
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return {"data": _to_permission_response(grant, role, service)}


@router.delete("/{role_id}/services/{service_type_id}")
def revoke_role_service_permission(role_id: str, service_type_id: str, db: Session = Depends(get_db)):
    grant = db.query(RoleServicePermission).filter_by(role_id=role_id, service_type_id=service_type_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Role does not have access to this service")

    db.delete(grant)
    db.commit()
    return {"data": {"message": "role access revoked"}}
