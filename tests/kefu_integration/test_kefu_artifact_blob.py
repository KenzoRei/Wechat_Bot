"""
Stored Kefu invoice files (V37; invoice-inventory plan, Phase 2 "Replay
stability", decisions D8/D9).

- enqueue_file stores an invoice workbook's exact bytes once;
- the loader returns those bytes, never a rebuild from live data;
- the purge keeps a file while a delivery of it is pending, and for 30 days;
- a duplicate-message replay of a purged file is skipped, never rebuilt and
  never an error.

Real Postgres DB; every row created here is removed by exact id.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from database import SessionLocal
from models.group import GroupConfig

DOC = "invoice_workbook"


@pytest.fixture
def env():
    from models.kefu import KefuStaff
    from models.request_log import RequestLog
    from models.role import Role

    db = SessionLocal()
    group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
    role = db.query(Role).filter_by(name="customer").one()
    staff = KefuStaff(open_kfid=f"kf-blobtest-{uuid.uuid4().hex[:8]}",
                      external_userid=f"staff-blobtest-{uuid.uuid4().hex[:8]}",
                      group_id=group.group_id, role_id=role.role_id, display_name="blob test")
    log = RequestLog(group_id=group.group_id, status="success", raw_message="blob test", source_channel="kefu")
    db.add_all([staff, log])
    db.commit()
    yield db, staff.staff_id, log.log_id
    db.rollback()
    db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id = :s"), {"s": staff.staff_id})
    db.execute(text("delete from kefu_artifact_blob where artifact_key like :k"), {"k": f"{log.log_id}:%"})
    db.execute(text("delete from request_log where log_id = :l"), {"l": log.log_id})
    db.execute(text("delete from kefu_staff where staff_id = :s"), {"s": staff.staff_id})
    db.commit()
    db.close()


def _artifact(log_id, content=b"original workbook bytes"):
    return {"bytes": content, "filename": "invoice.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "artifact_key": f"{log_id}:{DOC}"}


def _enqueue(db, staff_id, log_id, artifact, suffix="a"):
    from core.kefu_delivery import enqueue_file
    delivery = enqueue_file(db, recipient_staff_id=staff_id, idempotency_key=f"blobtest:{log_id}:{suffix}",
                            request_log_id=log_id, doc_type=DOC, artifact=artifact)
    db.commit()
    return delivery


def test_loader_returns_stored_bytes_and_never_rebuilds(env, monkeypatch):
    from core import uchoice_invoice_export
    from core.kefu_artifact_loader import load_artifact
    from core.kefu_delivery import content_hash

    db, staff_id, log_id = env
    delivery = _enqueue(db, staff_id, log_id, _artifact(log_id))

    def _must_not_rebuild(*args, **kwargs):
        raise AssertionError("invoice rebuilt from live data")
    monkeypatch.setattr(uchoice_invoice_export, "build_invoice_artifact", _must_not_rebuild)

    loaded = load_artifact(log_id, DOC, f"{log_id}:{DOC}")
    assert loaded.content == b"original workbook bytes"
    assert content_hash(loaded.content) == delivery.payload_hash


def test_same_key_different_bytes_is_rejected(env):
    db, staff_id, log_id = env
    _enqueue(db, staff_id, log_id, _artifact(log_id))
    with pytest.raises(ValueError, match="artifact_key_collision"):
        _enqueue(db, staff_id, log_id, _artifact(log_id, b"different bytes"), suffix="b")
    db.rollback()


def test_purge_keeps_pending_and_recent_files(env):
    from core.kefu_delivery import purge_expired_artifact_blobs

    db, staff_id, log_id = env
    delivery = _enqueue(db, staff_id, log_id, _artifact(log_id))
    key = f"{log_id}:{DOC}"
    db.execute(text("update kefu_artifact_blob set created_at = now() - interval '40 days' where artifact_key = :k"),
               {"k": key})
    db.commit()

    purge_expired_artifact_blobs(db)          # delivery still pending -> kept
    db.commit()
    assert db.execute(text("select count(*) from kefu_artifact_blob where artifact_key = :k"), {"k": key}).scalar() == 1

    db.execute(text("update kefu_outbound_delivery set status = 'sent' where delivery_id = :d"),
               {"d": delivery.delivery_id})
    db.commit()
    purge_expired_artifact_blobs(db, now=datetime.now(timezone.utc) - timedelta(days=20))  # not yet 30 days
    db.commit()
    assert db.execute(text("select count(*) from kefu_artifact_blob where artifact_key = :k"), {"k": key}).scalar() == 1

    purge_expired_artifact_blobs(db)          # sent and 40 days old -> purged
    db.commit()
    assert db.execute(text("select count(*) from kefu_artifact_blob where artifact_key = :k"), {"k": key}).scalar() == 0


def test_replay_skips_a_purged_file_instead_of_failing(env, monkeypatch):
    from types import SimpleNamespace

    from core import uchoice_invoice_export
    from core.kefu_artifact_loader import ArtifactUnavailable, load_artifact
    from core.kefu_case_adapter import _load_replay_artifacts

    db, _staff_id, log_id = env
    key = f"{log_id}:{DOC}"
    monkeypatch.setattr(uchoice_invoice_export, "build_invoice_artifact",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt on replay")))

    with pytest.raises(ArtifactUnavailable):
        load_artifact(log_id, DOC, key, allow_rebuild=False)
    assert _load_replay_artifacts(SimpleNamespace(request_log_id=log_id), [key]) == ()
