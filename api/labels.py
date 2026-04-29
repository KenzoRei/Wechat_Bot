import base64
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from database import get_db
from models.request_log import RequestLog

router = APIRouter()


@router.get("/labels/{serial_number}")
def download_label(serial_number: str, db: Session = Depends(get_db)):
    """
    Serves the FedEx/UPS label PDF for a completed shipment.
    No auth required — URL is shared directly with the customer via WeChat.
    The serial number acts as the access token (not guessable).
    """
    log = db.query(RequestLog).filter_by(serial_number=serial_number).first()
    if not log:
        raise HTTPException(status_code=404, detail="Serial number not found")

    result = log.result or {}
    label_b64 = result.get("label_base64", "")

    if not label_b64:
        raise HTTPException(status_code=404, detail="Label not available for this shipment")

    try:
        pdf_bytes = base64.b64decode(label_b64)
    except Exception:
        raise HTTPException(status_code=500, detail="Label data corrupted")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{serial_number}.pdf"'
        }
    )
