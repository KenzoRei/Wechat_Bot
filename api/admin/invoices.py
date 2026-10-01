from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io

import config
from database import get_db
from middleware.admin_auth import verify_admin_key
from core.uchoice_invoice_export import build_invoice_workbook, invoice_filename
from core.download_tokens import create_token
from core.uchoice_constants import VALID_WAREHOUSE_CODES

router = APIRouter(prefix="/admin/invoices", dependencies=[Depends(verify_admin_key)])

_SERVER_BASE_URL = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")


def _parse_warehouses(warehouse_code: str) -> list[str]:
    """
    warehouse_code: one code (JFK), a comma list (JFK,DE) or "all". Every
    code must be a real warehouse -- an unknown one would otherwise build an
    all-zero invoice (exact-match filters) and be echoed into the filename.
    Same rule as core.pre_confirm_validators._valid_invoice_warehouses.
    """
    raw = [part.strip() for part in (warehouse_code or "").split(",") if part.strip()]
    if len(raw) == 1 and raw[0].lower() == "all":
        return sorted(VALID_WAREHOUSE_CODES)
    if not raw or any(code not in VALID_WAREHOUSE_CODES for code in raw):
        raise HTTPException(
            status_code=400,
            detail=f"warehouse_code must be one or more of: {', '.join(sorted(VALID_WAREHOUSE_CODES))} "
                   f"(comma-separated), or all",
        )
    return sorted(dict.fromkeys(raw))


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
    response shows. warehouse_code: JFK, DE or NJ, a comma list (JFK,DE), or all.
    start_month/end_month: 'YYYY-MM'.
    """
    warehouse_codes = _parse_warehouses(warehouse_code)
    try:
        data = build_invoice_workbook(db, warehouse_codes, start_month, end_month)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_month/end_month must be 'YYYY-MM'")

    filename = invoice_filename(warehouse_codes, start_month, end_month)
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
    warehouse_codes = _parse_warehouses(warehouse_code)
    try:
        data = build_invoice_workbook(db, warehouse_codes, start_month, end_month)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_month/end_month must be 'YYYY-MM'")

    filename = invoice_filename(warehouse_codes, start_month, end_month)
    token = create_token(
        data, filename,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return {"data": {
        "download_url": f"{_SERVER_BASE_URL}/files/download/{token}",
        "expires_in_seconds": 3600,
    }}
