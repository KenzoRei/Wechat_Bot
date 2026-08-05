from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from core.download_tokens import get_token

router = APIRouter()


@router.get("/files/download/{token}")
def download_file(token: str):
    """
    Serves any short-lived file generated via core/download_tokens.py —
    invoice workbooks (GET /admin/invoices/export-link) and delivery-order
    PDFs (confirm_outbound_completion) both use this. No auth required — the
    token itself (32 random bytes) is the access control, same pattern as
    api/labels.py's serial_number-as-token download.
    """
    entry = get_token(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="Link not found or expired")

    return Response(
        content=entry["data"],
        media_type=entry["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )
