"""
Scheduled sweep over pending core/kefu_delivery.py rows -- the piece that
was still missing after core/kefu_case_adapter.py durably enqueues a
reply: something has to actually call deliver_one() for each pending row.
Registered in main.py as a periodic job, gated behind KEFU_ENABLED,
mirroring jobs/session_expiry.py's own wrapper-opens-its-own-session
pattern.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from clients.kefu_client import KefuClient
from core.kefu_delivery import ArtifactLoader, deliver_one
from models.kefu import KefuOutboundDelivery


def run_delivery_sweep(
    db_factory: Callable[[], Session],
    client: KefuClient,
    artifact_loader: ArtifactLoader,
    *,
    limit: int = 50,
) -> int:
    """
    Attempts every currently-pending delivery once. deliver_one() itself
    already no-ops cleanly (rolls back, returns None) for a row whose
    next_retry_at hasn't arrived yet or that another worker just claimed
    via the advisory lock, so a plain sweep over every 'pending' row is
    safe to call frequently without its own due-time filtering here.
    """
    list_db = db_factory()
    try:
        delivery_ids = list(
            list_db.execute(
                select(KefuOutboundDelivery.delivery_id)
                .where(KefuOutboundDelivery.status == "pending")
                .order_by(KefuOutboundDelivery.created_at)
                .limit(limit)
            ).scalars()
        )
    finally:
        list_db.close()

    attempted = 0
    for delivery_id in delivery_ids:
        db = db_factory()
        try:
            deliver_one(db, client, delivery_id=delivery_id, artifact_loader=artifact_loader)
            attempted += 1
        except Exception as e:
            print(f"[kefu_delivery_worker] delivery {delivery_id} failed unexpectedly: {e}", flush=True)
        finally:
            db.close()
    return attempted
