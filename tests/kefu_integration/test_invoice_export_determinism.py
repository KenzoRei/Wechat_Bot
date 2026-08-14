"""
Verifies the invoice Excel export is byte-identical across separate calls
for the same underlying request -- required for Kefu's durable delivery
queue, which verifies a content hash before every send
(core/kefu_delivery.py's artifact_hash_mismatch check) and regenerates the
artifact from scratch on redelivery (core/kefu_artifact_loader.py), rather
than storing raw bytes.

Real Postgres DB (RequestLog.created_at is exactly what's under test --
a mock can't stand in for it). Fail-closed, exact-row cleanup per the
established pattern; never bulk-deletes by the shared test identity.
"""
import time
import datetime

import pytest
from sqlalchemy import text

from database import SessionLocal

OPENID = "transworld"
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


_created_log_ids: list = []


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    db.rollback()
    for lid in _created_log_ids:
        db.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
    _created_log_ids.clear()
    db.commit()


def _make_invoice_log(db, group_id, created_at):
    from core import request_logger
    service_type_id = db.execute(text(
        "select service_type_id from service_type where name = 'view_invoice'"
    )).scalar()
    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=group_id, service_type_id=service_type_id,
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)
    db.execute(text("update request_log set created_at = :ts where log_id = :lid"),
               {"ts": created_at, "lid": log.log_id})
    db.commit()
    return log


def test_workbook_bytes_are_deterministic_for_explicit_generated_at(db):
    """Two builds with the same generated_at, spaced apart in wall-clock
    time, must produce byte-identical output -- proves the fix, not just
    the input contract."""
    from core.uchoice_invoice_export import build_invoice_workbook

    fixed = datetime.datetime(2026, 8, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
    first = build_invoice_workbook(db, "JFK", "2026-08", generated_at=fixed)
    time.sleep(1.1)
    second = build_invoice_workbook(db, "JFK", "2026-08", generated_at=fixed)

    assert first == second


def test_artifact_regeneration_matches_original_build_hash(db):
    """Simulates the real failure scenario: build the artifact once (as
    core/kefu_turn_apply.py does at turn time), then regenerate it later
    (as core/kefu_artifact_loader.py does for a deferred/retried delivery).
    The two must hash identically, or Kefu's delivery queue rejects the
    redelivery with artifact_hash_mismatch -- this is the exact bug that
    made every Kefu invoice export fail permanently before this fix."""
    from core import access_control
    from core.uchoice_invoice_export import build_invoice_artifact
    from core.kefu_delivery import content_hash

    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    fixed_past = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    log = _make_invoice_log(db, access.group_id, created_at=fixed_past)

    initial = build_invoice_artifact(db, "JFK", "2026-08", None, log.log_id)
    time.sleep(1.1)
    regenerated = build_invoice_artifact(db, "JFK", "2026-08", None, log.log_id)

    assert content_hash(initial["bytes"]) == content_hash(regenerated["bytes"])
    assert initial["artifact_key"] == regenerated["artifact_key"]
