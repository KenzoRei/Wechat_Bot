from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.role import Role
from api.schemas import RoleCreate, RoleResponse

router = APIRouter(prefix="/admin/roles", dependencies=[Depends(verify_admin_key)])


@router.get("")
def list_roles(db: Session = Depends(get_db)):
    roles = db.query(Role).order_by(Role.name).all()
    return {"data": [RoleResponse.model_validate(r) for r in roles]}


@router.post("", status_code=201)
def create_role(body: RoleCreate, db: Session = Depends(get_db)):
    existing = db.query(Role).filter_by(name=body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name already exists")

    role = Role(name=body.name, description=body.description)
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"data": RoleResponse.model_validate(role)}
