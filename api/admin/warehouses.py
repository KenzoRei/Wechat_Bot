from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from middleware.admin_auth import verify_admin_key
from core import warehouse_directory
from models.company_warehouse import CompanyWarehouse
from api.schemas import CompanyWarehouseCreate, CompanyWarehouseUpdate, CompanyWarehouseResponse

router = APIRouter(prefix="/admin/warehouses", dependencies=[Depends(verify_admin_key)])


def _to_response(record: warehouse_directory.WarehouseRecord) -> CompanyWarehouseResponse:
    return CompanyWarehouseResponse(**record.__dict__)


@router.get("")
def list_warehouses(db: Session = Depends(get_db)):
    records = warehouse_directory.list_warehouses(db)
    return {"data": [_to_response(r) for r in records]}


@router.get("/{warehouse_abbr}")
def get_warehouse(warehouse_abbr: str, db: Session = Depends(get_db)):
    record = warehouse_directory.get_warehouse(db, warehouse_abbr)
    if record is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    return {"data": _to_response(record)}


@router.post("", status_code=201)
def create_warehouse(body: CompanyWarehouseCreate, db: Session = Depends(get_db)):
    if db.get(CompanyWarehouse, body.warehouse_abbr):
        raise HTTPException(status_code=409, detail="warehouse_abbr already exists")

    fields = body.model_dump(exclude={"warehouse_abbr", "created_by"})
    record = warehouse_directory.upsert_warehouse(db, body.warehouse_abbr, actor=body.created_by, **fields)
    return {"data": _to_response(record)}


@router.patch("/{warehouse_abbr}")
def update_warehouse(warehouse_abbr: str, body: CompanyWarehouseUpdate, db: Session = Depends(get_db)):
    if warehouse_directory.get_warehouse(db, warehouse_abbr) is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")

    fields = body.model_dump(exclude={"updated_by"}, exclude_none=True)
    record = warehouse_directory.upsert_warehouse(db, warehouse_abbr, actor=body.updated_by, **fields)
    return {"data": _to_response(record)}
