from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.group import GroupConfig, GroupService, GroupServiceRole
from models.service import ServiceType
from models.workflow import Workflow
from models.role import Role
from api.schemas import (
    GroupServiceCreate, GroupServiceResponse,
    GroupServiceRoleGrant, GroupServiceRoleResponse,
)

router = APIRouter(prefix="/admin/groups", dependencies=[Depends(verify_admin_key)])


def _validate_config(config: dict, schema: dict) -> list[str]:
    """Returns list of missing required keys."""
    return [key for key in schema.get("required", []) if key not in config]


@router.post("/{group_id}/services", status_code=201)
def assign_service(group_id: str, body: GroupServiceCreate, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    service_type = db.query(ServiceType).filter_by(
        service_type_id=body.service_type_id
    ).first()
    if not service_type:
        raise HTTPException(status_code=404, detail="service_type_id not found")

    workflow = db.query(Workflow).filter_by(workflow_id=body.workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_id not found")

    # validate config against group_config_schema
    missing = _validate_config(body.config, service_type.group_config_schema)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"config is missing required keys: {missing}"
        )

    existing = db.query(GroupService).filter_by(
        group_id=group_id, service_type_id=body.service_type_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Service already assigned to this group")

    gs = GroupService(
        group_id=group_id,
        service_type_id=body.service_type_id,
        workflow_id=body.workflow_id,
        config=body.config,
    )
    db.add(gs)
    db.commit()

    return {"data": GroupServiceResponse(
        group_id=gs.group_id,
        service_type_id=gs.service_type_id,
        service_name=service_type.name,
        workflow_id=gs.workflow_id,
        workflow_name=workflow.name,
        config=gs.config,
    )}


@router.get("/{group_id}/services")
def list_services(group_id: str, db: Session = Depends(get_db)):
    group = db.query(GroupConfig).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rows = (
        db.query(GroupService, ServiceType, Workflow)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
        .join(Workflow, GroupService.workflow_id == Workflow.workflow_id)
        .filter(GroupService.group_id == group_id)
        .all()
    )

    return {"data": [
        GroupServiceResponse(
            group_id=gs.group_id,
            service_type_id=gs.service_type_id,
            service_name=st.name,
            workflow_id=gs.workflow_id,
            workflow_name=wf.name,
            config=gs.config,
        )
        for gs, st, wf in rows
    ]}


@router.delete("/{group_id}/services/{service_type_id}")
def remove_service(group_id: str, service_type_id: str, db: Session = Depends(get_db)):
    gs = db.query(GroupService).filter_by(
        group_id=group_id, service_type_id=service_type_id
    ).first()
    if not gs:
        raise HTTPException(status_code=404, detail="Service not assigned to this group")

    db.delete(gs)
    db.commit()
    return {"data": {"message": "service removed from group"}}


# ── Service permission grants (deny-by-default) ────────────────────────────────
# A (group_id, service_type_id) is invisible to a role unless a matching row
# exists here. See docs/data-model.md "Role-Based Service Permissions".

@router.post("/{group_id}/services/{service_type_id}/roles", status_code=201)
def grant_role(
    group_id: str, service_type_id: str, body: GroupServiceRoleGrant,
    db: Session = Depends(get_db)
):
    gs = db.query(GroupService).filter_by(
        group_id=group_id, service_type_id=service_type_id
    ).first()
    if not gs:
        raise HTTPException(status_code=404, detail="Service not assigned to this group")

    role = db.query(Role).filter_by(name=body.role).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Unknown role: '{body.role}'. See GET /admin/roles")

    existing = db.query(GroupServiceRole).filter_by(
        group_id=group_id, service_type_id=service_type_id, role_id=role.role_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role already granted access to this service")

    grant = GroupServiceRole(
        group_id=group_id,
        service_type_id=service_type_id,
        role_id=role.role_id,
        created_by=body.created_by,
    )
    db.add(grant)
    db.commit()

    return {"data": GroupServiceRoleResponse(
        group_id=grant.group_id,
        service_type_id=grant.service_type_id,
        role=role.name,
        created_by=grant.created_by,
        created_at=grant.created_at,
    )}


@router.get("/{group_id}/services/{service_type_id}/roles")
def list_role_grants(group_id: str, service_type_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(GroupServiceRole, Role)
        .join(Role, GroupServiceRole.role_id == Role.role_id)
        .filter(
            GroupServiceRole.group_id == group_id,
            GroupServiceRole.service_type_id == service_type_id,
        )
        .all()
    )
    return {"data": [
        GroupServiceRoleResponse(
            group_id=g.group_id,
            service_type_id=g.service_type_id,
            role=r.name,
            created_by=g.created_by,
            created_at=g.created_at,
        )
        for g, r in rows
    ]}


@router.delete("/{group_id}/services/{service_type_id}/roles/{role_name}")
def revoke_role(group_id: str, service_type_id: str, role_name: str, db: Session = Depends(get_db)):
    role = db.query(Role).filter_by(name=role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Unknown role: '{role_name}'")

    grant = db.query(GroupServiceRole).filter_by(
        group_id=group_id, service_type_id=service_type_id, role_id=role.role_id
    ).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Role does not have access to this service")

    db.delete(grant)
    db.commit()
    return {"data": {"message": "role access revoked"}}
