from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

from database import SessionLocal
from jobs.session_expiry import run_expiry_check
from api import health, webhook, labels
from api.admin import groups, members, services, reference, logs, sessions, seed_v6


# ── Scheduler setup ───────────────────────────────────────────────────────────

def _run_expiry_job():
    """Wrapper so the scheduler can open its own DB session."""
    db = SessionLocal()
    try:
        run_expiry_check(db)
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(_run_expiry_job, "interval", minutes=5, id="session_expiry")


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Logistics WeChat Bot Platform",
    version="1.0.0",
    lifespan=lifespan
)

# public routes
app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(labels.router)

# admin routes
app.include_router(groups.router)
app.include_router(members.router)
app.include_router(services.router)
app.include_router(reference.router)
app.include_router(logs.router)
app.include_router(sessions.router)
app.include_router(seed_v6.router)
