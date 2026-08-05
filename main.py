from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

from database import SessionLocal
from jobs.session_expiry import run_expiry_check
from jobs.uchoice_daily import run_uchoice_daily
from jobs.uchoice_invoice import run_uchoice_invoice
from api import health, webhook, labels, admin_panel, file_download
from api.admin import groups, members, services, reference, logs, sessions, roles, invoices


# ── Scheduler setup ───────────────────────────────────────────────────────────

def _run_expiry_job():
    """Wrapper so the scheduler can open its own DB session."""
    db = SessionLocal()
    try:
        run_expiry_check(db)
    finally:
        db.close()


def _run_uchoice_daily_job():
    db = SessionLocal()
    try:
        run_uchoice_daily(db)
    finally:
        db.close()


def _run_uchoice_invoice_job():
    db = SessionLocal()
    try:
        run_uchoice_invoice(db)
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(_run_expiry_job, "interval", minutes=5, id="session_expiry")
scheduler.add_job(_run_uchoice_daily_job, "cron", hour=8, id="uchoice_daily")
scheduler.add_job(_run_uchoice_invoice_job, "cron", day=1, hour=9, id="uchoice_invoice")


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
app.include_router(file_download.router)
app.include_router(admin_panel.router)  # public route — the page itself prompts for the admin key client-side

# admin routes
app.include_router(groups.router)
app.include_router(members.router)
app.include_router(services.router)
app.include_router(reference.router)
app.include_router(logs.router)
app.include_router(sessions.router)
app.include_router(roles.router)
app.include_router(invoices.router)
