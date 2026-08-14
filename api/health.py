from fastapi import APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from sqlalchemy import text

import config
from database import SessionLocal

router = APIRouter()


@router.get("/health")
def health_check():
    """Kept as a liveness alias for existing callers (e.g. the admin panel)."""
    return _live_body()


@router.get("/health/live")
def health_live():
    """Process is up and serving requests. Does not touch the database."""
    return _live_body()


def _live_body() -> dict:
    return {"data": {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}}


@router.get("/health/ready")
def health_ready():
    """
    Signed cross-review plan, Section C6: liveness split from readiness.
    Checks the one dependency every request-handling path actually needs
    (a working DB connection) and reports which channels are configured to
    run, so "is this deployment actually able to do its job" is answerable
    without reading logs.

    Plain `def` (not `async def`) is deliberate: FastAPI runs sync route
    functions in a worker thread pool, so the blocking DB call below never
    ties up the event loop the way it would inside an `async def` handler.

    Returns HTTP 503 (not 200) when not ready -- most load balancers and
    orchestrators key readiness off the status code, not a JSON field a
    misconfigured check might never look at. The raw exception is logged
    server-side only; the public response gets a generic reason so a
    connection string, hostname, or driver detail is never exposed through
    this unauthenticated endpoint.
    """
    checks: dict[str, object] = {}
    ok = True

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        print(f"[health] readiness DB check failed: {e}", flush=True)
        checks["database"] = "error"
        ok = False
    finally:
        db.close()

    checks["smart_robot_enabled"] = config.SMART_ROBOT_ENABLED
    checks["kefu_enabled"] = config.KEFU_ENABLED
    checks["kefu_callback_enabled"] = config.KEFU_CALLBACK_ENABLED
    checks["run_scheduler"] = config.RUN_SCHEDULER

    body = {
        "data": {
            "status": "ok" if ok else "degraded",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }
    return JSONResponse(content=body, status_code=200 if ok else 503)
