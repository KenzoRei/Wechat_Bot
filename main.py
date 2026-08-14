"""
This process owns the deployment's BackgroundScheduler. Running more than one
web/worker process against the same database at once will duplicate every
scheduled job below (session expiry, uChoice reports, Kefu polling) --
there is no leader election or cross-process coordination. Keep this a
single-instance deployment (Render: one instance, no horizontal scaling)
until that coordination exists.

RUN_SCHEDULER (config.py) defaults true and is a useful immediate seam, but
it is not automatically enforced across instances: two processes can each
default it to true and start a scheduler. Operationally:
  - Set RUN_SCHEDULER explicitly in Render's environment even for today's
    single instance, so it's a deliberate, visible choice rather than an
    unset default.
  - If a second instance/replica is ever added, exactly one may have
    RUN_SCHEDULER=true; every other one must be explicitly false.
  - /health/ready reports RUN_SCHEDULER per-instance, so a misconfigured
    topology (zero or multiple scheduler owners) is at least observable.
  - If real horizontal scaling is needed later, prefer moving the scheduler
    into its own separate worker process/service rather than continuing to
    coordinate this flag by hand across web instances.
"""
import os
import uuid

from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

import config
from database import SessionLocal
from jobs.session_expiry import run_expiry_check
from jobs.uchoice_daily import run_uchoice_daily
from jobs.uchoice_invoice import run_uchoice_invoice
from api import health, labels, admin_panel, file_download
from api.admin import groups, members, services, reference, logs, sessions, roles, invoices, kefu_staff


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


# Instance-unique worker identity -- set WORKER_INSTANCE_ID
# explicitly in the deployment environment if you ever do run more than one
# instance, so leases/logs are attributable; falls back to a random ID per
# process start so a single-instance deployment still isn't hardcoded.
_WORKER_INSTANCE_ID = os.getenv("WORKER_INSTANCE_ID") or f"worker-{uuid.uuid4().hex[:8]}"

# max_instances=1 + coalesce=True on every job below: a slow run must never
# overlap with the next tick of the same job, and if ticks were missed
# (e.g. a deploy restart), catch up with a single run rather than a burst.
scheduler = BackgroundScheduler()
scheduler.add_job(
    _run_expiry_job, "interval", minutes=5, id="session_expiry",
    max_instances=1, coalesce=True, misfire_grace_time=60,
)
# These jobs stay registered while Smart Robot is enabled. Their own queries
# are additionally filtered to
# source_channel='smart_robot' (jobs/uchoice_daily.py, jobs/uchoice_invoice.py).
if config.SMART_ROBOT_ENABLED:
    scheduler.add_job(
        _run_uchoice_daily_job, "cron", hour=8, id="uchoice_daily",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_uchoice_invoice_job, "cron", day=1, hour=9, id="uchoice_invoice",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )


# ── Smart Robot wiring (feature-gated) ─────────────────────────────────────────
# Unlike Kefu's callback, Smart Robot's webhook has no analogous "verify the
# URL before a secret is issued" bootstrap requirement, so there is no reason
# for its route (or the AI providers api/webhook.py constructs at import
# time) to exist at all when the channel is disabled -- the whole module is
# only imported when enabled.
_smart_robot_router = None
if config.SMART_ROBOT_ENABLED:
    from api import webhook as _webhook
    _smart_robot_router = _webhook.router


# ── Kefu wiring (feature-gated) ──────────────────────────────────────────────
# Two independent modes: callback
# verification/crypto/route (KEFU_CALLBACK_ENABLED, needed before WeCom will
# issue the API Secret) is now separate from business processing
# (KEFU_ENABLED, clients/workers/scheduled jobs). A Smart-Bot-only
# deployment can set both false and needs none of the Kefu credentials --
# config.py enforces KEFU_ENABLED implies KEFU_CALLBACK_ENABLED.
_kefu_router = None
if config.KEFU_CALLBACK_ENABLED:
    from core.WXBizXmlMsgCrypt import WXBizXmlMsgCrypt
    from api.kefu_callback import create_kefu_callback_router

    _kefu_crypt = WXBizXmlMsgCrypt(
        config.WECHAT_KEFU_TOKEN, config.WECHAT_KEFU_ENCODING_AES_KEY, config.WECHAT_CORP_ID
    )

    def _on_kefu_sync_event(event):
        """Acknowledge verified callbacks while business processing is disabled."""
        print("[main] Kefu callback verified; processing is disabled", flush=True)

if config.KEFU_ENABLED:
    from clients.kefu_client import KefuClient
    from core import kefu_artifact_loader, kefu_case_adapter, kefu_delivery_worker, kefu_sync

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

    def _run_kefu_worker_job():
        kefu_sync.run_worker_once(SessionLocal, _kefu_processor, worker_id=_WORKER_INSTANCE_ID)

    def _run_kefu_delivery_job():
        kefu_delivery_worker.run_delivery_sweep(SessionLocal, _kefu_client, kefu_artifact_loader.load_artifact)

    # Kefu's reply-window and quota semantics govern how many messages can be
    # sent per window, not how
    # often we're allowed to poll; there's no WeCom-imposed floor on these.
    # Tightened from 15s/20s after live latency observed as slow.
    # max_instances=1 matters most here: at a 2-3s interval, an occasional
    # slow run must not stack a second overlapping run on top of itself.
    scheduler.add_job(
        _run_kefu_worker_job, "interval", seconds=3, id="kefu_worker",
        max_instances=1, coalesce=True, misfire_grace_time=5,
    )
    scheduler.add_job(
        _run_kefu_delivery_job, "interval", seconds=2, id="kefu_delivery",
        max_instances=1, coalesce=True, misfire_grace_time=5,
    )

if config.KEFU_CALLBACK_ENABLED:
    _kefu_router = create_kefu_callback_router(_kefu_crypt, _on_kefu_sync_event)


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.RUN_SCHEDULER:
        scheduler.start()
    else:
        print("[main] RUN_SCHEDULER=false -- this process will not run scheduled jobs", flush=True)
    yield
    if config.RUN_SCHEDULER:
        scheduler.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Logistics WeChat Bot Platform",
    version="1.0.0",
    lifespan=lifespan
)

# public routes
app.include_router(health.router)
if _smart_robot_router is not None:
    app.include_router(_smart_robot_router)
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
app.include_router(kefu_staff.router)
