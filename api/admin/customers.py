from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core import customer_directory
from models.customer import Customer, CustomerCredential
from api.schemas import CustomerCreate, CustomerUpdate, CustomerResponse, CustomerCredentialSet

router = APIRouter(prefix="/admin/customers", dependencies=[Depends(verify_admin_key)])

_VALID_STATUSES = {"pending", "active", "inactive"}
_VALID_CREDENTIAL_TYPES = {"oms_app_key", "oms_app_secret", "ydd_username", "ydd_password"}


def _to_response(record: customer_directory.CustomerRecord) -> CustomerResponse:
    return CustomerResponse(**record.__dict__)


@router.get("")
def list_customers(status: str | None = Query(None), db: Session = Depends(get_db)):
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status!r}. Allowed: {sorted(_VALID_STATUSES)}")
    records = customer_directory.list_customers(db, status=status)
    return {"data": [_to_response(r) for r in records]}


@router.get("/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db)):
    record = customer_directory.get_customer(db, customer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"data": _to_response(record)}


@router.post("", status_code=201)
def create_customer(body: CustomerCreate, db: Session = Depends(get_db)):
    if db.get(Customer, body.customer_id):
        raise HTTPException(status_code=409, detail="customer_id already exists")
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status!r}. Allowed: {sorted(_VALID_STATUSES)}")

    fields = body.model_dump(exclude={"customer_id", "created_by"})
    record = customer_directory.upsert_customer(db, body.customer_id, actor=body.created_by, **fields)
    return {"data": _to_response(record)}


@router.patch("/{customer_id}")
def update_customer(customer_id: str, body: CustomerUpdate, db: Session = Depends(get_db)):
    if customer_directory.get_customer(db, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.status is not None and body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status!r}. Allowed: {sorted(_VALID_STATUSES)}")

    fields = body.model_dump(exclude={"updated_by"}, exclude_none=True)
    record = customer_directory.upsert_customer(db, customer_id, actor=body.updated_by, **fields)
    return {"data": _to_response(record)}


@router.get("/{customer_id}/credentials")
def list_customer_credential_status(customer_id: str, db: Session = Depends(get_db)):
    """
    Metadata only -- which credential types are set, and when. Never the
    value itself, encrypted or otherwise; this endpoint exists so the admin
    panel can render "•••• (set on <date>)" without ever touching a secret.
    """
    if customer_directory.get_customer(db, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    rows = db.query(CustomerCredential).filter_by(customer_id=customer_id).all()
    return {"data": [
        {"credential_type": r.credential_type, "updated_at": r.updated_at, "updated_by": r.updated_by or r.created_by}
        for r in rows
    ]}


@router.post("/{customer_id}/credentials", status_code=201)
def set_customer_credential(customer_id: str, body: CustomerCredentialSet, db: Session = Depends(get_db)):
    """
    Write-only: sets or rotates one credential. Never echoes the value back
    -- the response confirms only that the write happened.
    """
    if customer_directory.get_customer(db, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.credential_type not in _VALID_CREDENTIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid credential_type: {body.credential_type!r}. Allowed: {sorted(_VALID_CREDENTIAL_TYPES)}")
    if not body.value:
        raise HTTPException(status_code=400, detail="value must not be empty")

    customer_directory.set_credential(db, customer_id, body.credential_type, body.value, actor=body.updated_by)
    return {"data": {"credential_type": body.credential_type, "message": "credential set"}}
