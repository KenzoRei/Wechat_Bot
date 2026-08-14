"""
Verifies the storage-history Excel export is byte-identical across
separate calls for the same underlying request -- same requirement as
tests/kefu_integration/test_invoice_export_determinism.py, same reason:
Kefu's durable delivery queue verifies a content hash before every send
and regenerates the artifact from scratch on redelivery
(core/kefu_artifact_loader.py), rather than storing raw bytes.

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


def _make_history_log(db, group_id, created_at):
    from core import request_logger
    service_type_id = db.execute(text(
        "select service_type_id from service_type where name = 'view_storage_history'"
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
    from core.uchoice_storage_history_export import build_storage_history_workbook

    fixed = datetime.datetime(2026, 8, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
    first = build_storage_history_workbook(db, "JFK", "2026-08", generated_at=fixed)
    time.sleep(1.1)
    second = build_storage_history_workbook(db, "JFK", "2026-08", generated_at=fixed)

    assert first == second


def test_artifact_regeneration_matches_original_build_hash(db):
    """Simulates the real Kefu scenario: build once at turn time
    (core/kefu_turn_apply.py), regenerate later for a deferred/retried
    delivery (core/kefu_artifact_loader.py). Must hash identically."""
    from core import access_control
    from core.uchoice_storage_history_export import build_storage_history_artifact
    from core.kefu_delivery import content_hash

    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    fixed_past = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    log = _make_history_log(db, access.group_id, created_at=fixed_past)

    initial = build_storage_history_artifact(db, "JFK", "2026-08", None, log.log_id)
    time.sleep(1.1)
    regenerated = build_storage_history_artifact(db, "JFK", "2026-08", None, log.log_id)

    assert content_hash(initial["bytes"]) == content_hash(regenerated["bytes"])
    assert initial["artifact_key"] == regenerated["artifact_key"]
