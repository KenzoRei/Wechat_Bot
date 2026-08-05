from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io

import config
from database import get_db
from middleware.admin_auth import verify_admin_key
from core.uchoice_invoice_export import build_invoice_workbook
from core.download_tokens import create_token

router = APIRouter(prefix="/admin/invoices", dependencies=[Depends(verify_admin_key)])

_SERVER_BASE_URL = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")


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


@router.get("/export-link")
def export_invoice_link(
    warehouse_code: str,
    start_month: str,
    end_month: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Same workbook as /export, but instead of streaming the file directly
    (which requires the X-Admin-Key header — not something a plain browser
    link can send), generates it once and returns a short-lived, unguessable
    download URL that needs no auth to open. Mirrors how FedEx/UPS label
    downloads already work (api/labels.py) — the random token in the URL is
    itself the entire access control, standing in for the admin key so this
    one link is safe to open/share without exposing the real credential.
    """
    try:
        data = build_invoice_workbook(db, warehouse_code, start_month, end_month)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_month/end_month must be 'YYYY-MM'")

    filename = f"invoice_{warehouse_code}_{start_month}_{end_month or start_month}.xlsx"
    token = create_token(
        data, filename,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return {"data": {
        "download_url": f"{_SERVER_BASE_URL}/files/download/{token}",
        "expires_in_seconds": 3600,
    }}
