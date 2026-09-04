from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES, VALID_WAREHOUSE_CODES
from models.role import Role
from api.schemas import RoleCreate, RoleResponse

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
