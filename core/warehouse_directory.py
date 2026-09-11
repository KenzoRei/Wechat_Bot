"""
Company warehouse master-data module boundary -- callers (handlers, admin
routes, AI-context assembly) go through these functions only, never a raw
query against company_warehouse from elsewhere. Mirrors
core/customer_directory.py's shape.
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session as DBSession

from models.company_warehouse import CompanyWarehouse


@dataclass
class WarehouseRecord:
    warehouse_abbr: str
    company_name: str
    addr: str
    city: str
    state: str
    zip_code: str
    contact: str | None
    phone: str | None
    email: str | None


def _to_record(row: CompanyWarehouse) -> WarehouseRecord:
    return WarehouseRecord(
        warehouse_abbr=row.warehouse_abbr,
        company_name=row.company_name,
        addr=row.addr,
        city=row.city,
        state=row.state,
        zip_code=row.zip_code,
        contact=row.contact,
        phone=row.phone,
        email=row.email,
    )


def get_warehouse(db: DBSession, warehouse_abbr: str) -> WarehouseRecord | None:
    row = db.get(CompanyWarehouse, warehouse_abbr)
    return _to_record(row) if row else None


def list_warehouses(db: DBSession) -> list[WarehouseRecord]:
    return [_to_record(row) for row in db.query(CompanyWarehouse).order_by(CompanyWarehouse.warehouse_abbr).all()]


def upsert_warehouse(db: DBSession, warehouse_abbr: str, actor: str, **fields) -> WarehouseRecord:
    """
    actor identifies who made this change (admin key holder) for
    created_by/updated_by, matching core/customer_directory.py's
    upsert_customer convention.
    """
    row = db.get(CompanyWarehouse, warehouse_abbr)
    if row is None:
        row = CompanyWarehouse(warehouse_abbr=warehouse_abbr, created_by=actor, **fields)
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_by = actor
    db.commit()
    db.refresh(row)
    return _to_record(row)


def warehouse_candidates(db: DBSession) -> list[dict]:
    """
    Plain-dict shape for injection into the AI's context (see
    core/session_manager.py's _build_uchoice_candidates and
    ai/prompt_builder.py's candidates_block) -- lets the AI resolve a bare
    abbreviation mention (e.g. "从LAX到DE") to real shipper_* OR
    recipient_* fields for fedex_label/ups_label (one of our own
    warehouses can be either side of a shipment), the same way
    sku_catalog/address_candidates already do for their own services.
    """
    return [
        {
            "warehouse_abbr": w.warehouse_abbr,
            "company_name": w.company_name,
            "addr": w.addr,
            "city": w.city,
            "state": w.state,
            "zip_code": w.zip_code,
            "contact": w.contact,
            "phone": w.phone,
        }
        for w in list_warehouses(db)
    ]
