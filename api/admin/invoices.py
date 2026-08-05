from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io

from database import get_db
from middleware.admin_auth import verify_admin_key
from core.uchoice_invoice_export import build_invoice_workbook

router = APIRouter(prefix="/admin/invoices", dependencies=[Depends(verify_admin_key)])


@router.get("/export")
def export_invoice(
    warehouse_code: str,
    start_month: str,
    end_month: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Downloads an .xlsx with the full detail behind an invoice (Summary +
    one row per contributing transaction) — not just the totals the chat
    response shows. warehouse_code: JFK or DE. start_month/end_month: 'YYYY-MM'.
    """
    try:
        data = build_invoice_workbook(db, warehouse_code, start_month, end_month)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_month/end_month must be 'YYYY-MM'")

    filename = f"invoice_{warehouse_code}_{start_month}_{end_month or start_month}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
