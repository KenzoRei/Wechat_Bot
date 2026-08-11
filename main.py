from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

import config
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
# kefu-migration-plan.md Sec 7: these stay registered and running as long as
# Smart Robot is enabled -- no change to their scheduling from this
# migration. Their own queries are additionally filtered to
# source_channel='smart_robot' (jobs/uchoice_daily.py, jobs/uchoice_invoice.py).
if config.SMART_ROBOT_ENABLED:
    scheduler.add_job(_run_uchoice_daily_job, "cron", hour=8, id="uchoice_daily")
    scheduler.add_job(_run_uchoice_invoice_job, "cron", day=1, hour=9, id="uchoice_invoice")


# ── Kefu wiring (feature-gated, Codex round-88's "application wiring") ────────
# Imports, credential-bound objects, and scheduled jobs stay entirely inside
# this block -- per Sec 6.4, a disabled channel must import/wire nothing at
# all, so an unconfigured KEFU_ENABLED=false deployment can never fail
# startup on account of Kefu-only code paths.
_kefu_router = None
if config.KEFU_ENABLED:
    from core.WXBizXmlMsgCrypt import WXBizXmlMsgCrypt
    from api.kefu_callback import create_kefu_callback_router
    from clients.kefu_client import KefuClient
    from core import kefu_artifact_loader, kefu_case_adapter, kefu_delivery_worker, kefu_sync

    # Per WeChat Work's documented Kefu callback contract, receive_id is the
    # CorpID (not the empty-string special case Smart Robot's own callback
    # uses -- core/webhook_receiver.py's _RECEIVE_ID -- Kefu is a company-
    # level integration, Smart Robot's callback is bot-scoped). Unverified
    # against the live API; flagged to Codex to confirm before this URL is
    # actually registered with WeChat.
    _kefu_crypt = WXBizXmlMsgCrypt(
        config.WECHAT_KEFU_TOKEN, config.WECHAT_KEFU_ENCODING_AES_KEY, config.WECHAT_CORP_ID
    )
    _kefu_client = KefuClient(config.WECHAT_CORP_ID, config.WECHAT_KEFU_SECRET)
    _kefu_processor = kefu_case_adapter.make_case_turn_processor(_kefu_client, SessionLocal)

    def _on_kefu_sync_event(event):
        """Fired from api/kefu_callback.py's background task on each verified callback."""
        if event.open_kfid and event.open_kfid != config.WECHAT_KEFU_OPEN_KFID:
            print(f"[main] Kefu sync event for unexpected open_kfid={event.open_kfid}, ignoring", flush=True)
            return
        try:
            kefu_sync.sync_available_messages(
                SessionLocal, _kefu_client,
                sync_token=event.sync_token, open_kfid=config.WECHAT_KEFU_OPEN_KFID,
            )
        except Exception as e:
            print(f"[main] Kefu sync failed: {e}", flush=True)

    _kefu_router = create_kefu_callback_router(_kefu_crypt, _on_kefu_sync_event)

    def _run_kefu_worker_job():
        kefu_sync.run_worker_once(SessionLocal, _kefu_processor, worker_id="kefu-worker-1")

    def _run_kefu_delivery_job():
        kefu_delivery_worker.run_delivery_sweep(SessionLocal, _kefu_client, kefu_artifact_loader.load_artifact)

    # Short intervals -- Kefu's own reply-window/quota semantics (plan
    # Sec 11.3) already govern actual send timing; these just keep the
    # claim -> process -> enqueue -> deliver pipeline moving promptly.
    scheduler.add_job(_run_kefu_worker_job, "interval", seconds=15, id="kefu_worker")
    scheduler.add_job(_run_kefu_delivery_job, "interval", seconds=20, id="kefu_delivery")


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
if _kefu_router is not None:
    app.include_router(_kefu_router)

# admin routes
app.include_router(groups.router)
app.include_router(members.router)
app.include_router(services.router)
app.include_router(reference.router)
app.include_router(logs.router)
app.include_router(sessions.router)
app.include_router(roles.router)
app.include_router(invoices.router)
