from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"data": {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}}
