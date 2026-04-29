from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.service import ServiceType
from models.workflow import Workflow, WorkflowStep
from api.schemas import ServiceTypeResponse, WorkflowResponse, WorkflowStepResponse

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_key)])


@router.get("/service-types")
def list_service_types(db: Session = Depends(get_db)):
    types = db.query(ServiceType).filter_by(is_active=True).all()
    return {"data": [ServiceTypeResponse.model_validate(st) for st in types]}


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db)):
    workflows = db.query(Workflow).order_by(Workflow.name).all()

    result = []
    for wf in workflows:
        steps = (
            db.query(WorkflowStep)
            .filter_by(workflow_id=wf.workflow_id)
            .order_by(WorkflowStep.step_order)
            .all()
        )
        result.append(WorkflowResponse(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            steps=[WorkflowStepResponse(step_order=s.step_order, step_type=s.step_type)
                   for s in steps]
        ))

    return {"data": result}
