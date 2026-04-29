from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.group import GroupConfig, GroupService
from models.service import ServiceType
from models.workflow import Workflow
from api.schemas import GroupServiceCreate, GroupServiceResponse

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
