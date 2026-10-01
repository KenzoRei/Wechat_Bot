"""
scripts/relabel_golive_opening.py (invoice-inventory plan, Phase 2b).

Seeds a go-live 库存盘点 (recount_storage) request plus later activity in
two test-only warehouses, then runs the script's own main() against the
test database with expectations for those warehouses. Cleans up exactly
the rows it created.

Real Postgres DB.
"""
import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from database import SessionLocal
from models.group import GroupConfig

WAREHOUSES = ("RLTEST1", "RLTEST2")
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"
GOLIVE = datetime(2031, 9, 1, 17, 29, tzinfo=timezone.utc)
LATER = datetime(2031, 9, 3, 21, 17, tzinfo=timezone.utc)

_spec = importlib.util.spec_from_file_location(
    "relabel_golive_opening", Path(__file__).resolve().parents[2] / "scripts" / "relabel_golive_opening.py")
relabel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relabel)


@pytest.fixture
def db():
    session = SessionLocal()
    created_logs = []
    session.info["created_logs"] = created_logs
    yield session
    session.rollback()
    for table in ("uchoice_storage_txn", "uchoice_storage"):
        session.execute(text(f"delete from {table} where warehouse_code = any(:w)"), {"w": list(WAREHOUSES)})
    for log_id in created_logs:
        session.execute(text("delete from request_log where log_id = :id"), {"id": log_id})
    session.commit()
    session.close()


def _request(db, service_name):
    from models.request_log import RequestLog
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    service_type_id = db.execute(text("select service_type_id from service_type where name = :n"),
                                 {"n": service_name}).scalar()
    log = RequestLog(group_id=group.group_id, status="success", raw_message=f"relabel-test-{uuid.uuid4().hex[:8]}",
                     source_channel="smart_robot", service_type_id=service_type_id)
    db.add(log)
    db.commit()
    db.info["created_logs"].append(log.log_id)
    return log.log_id


def _txn(db, wh, sku, bpp, delta, txn_type, when, request_log_id):
    db.execute(text("""
        insert into uchoice_storage_txn (warehouse_code, sku_code, boxes_per_pallet, pallet_delta, txn_type,
                                         request_log_id, created_by, created_at)
        values (:wh, :sku, :bpp, :d, :t, :rid, 'relabel-test', :at)"""),
        {"wh": wh, "sku": sku, "bpp": bpp, "d": delta, "t": txn_type, "rid": request_log_id, "at": when})
    params = {"wh": wh, "sku": sku, "bpp": bpp, "d": delta}
    updated = db.execute(text("""
        update uchoice_storage set pallet_count = pallet_count + :d
        where warehouse_code = :wh and sku_code = :sku and boxes_per_pallet = :bpp"""), params)
    if updated.rowcount == 0:
        db.execute(text("""
            insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count)
            values (:wh, :sku, :bpp, :d)"""), params)
    db.commit()


@pytest.fixture
def golive(db):
    """RLTEST1: go-live recount of 3 rows / 9 pallets, then a real outbound.
    RLTEST2: go-live recount of 1 row / 4 pallets."""
    r1 = _request(db, "recount_storage")
    _txn(db, "RLTEST1", "s2", 64, 5, "recount", GOLIVE, r1)
    _txn(db, "RLTEST1", "s2", 72, 3, "recount", GOLIVE, r1)
    _txn(db, "RLTEST1", "t1", 105, 1, "recount", GOLIVE, r1)
    out = _request(db, "confirm_outbound_completion")
    _txn(db, "RLTEST1", "s2", 64, -1, "outbound", LATER, out)
    r2 = _request(db, "recount_storage")
    _txn(db, "RLTEST2", "s5", 50, 4, "recount", GOLIVE, r2)
    return {"RLTEST1": (3, 9), "RLTEST2": (1, 4)}


def _url():
    return os.environ["DATABASE_URL"]


def _types(db):
    return db.execute(text("""
        select warehouse_code, txn_type, count(*), sum(pallet_delta) from uchoice_storage_txn
        where warehouse_code = any(:w) group by 1, 2 order by 1, 2"""), {"w": list(WAREHOUSES)}).all()


def test_dry_run_changes_nothing(db, golive):
    before = _types(db)
    assert relabel.main(["--database-url", _url()], expected=golive) == 0
    db.rollback()
    assert _types(db) == before


def test_apply_relabels_only_golive_rows_and_is_idempotent(db, golive):
    assert relabel.main(["--database-url", _url(), "--apply"], expected=golive) == 0
    db.rollback()
    assert _types(db) == [
        ("RLTEST1", "opening", 3, 9),
        ("RLTEST1", "outbound", 1, -1),
        ("RLTEST2", "opening", 1, 4),
    ]
    notes = db.execute(text("select distinct note from uchoice_storage_txn "
                            "where warehouse_code = any(:w) and txn_type = 'opening'"),
                       {"w": list(WAREHOUSES)}).scalars().all()
    assert notes == [relabel.NOTE]
    stock = db.execute(text("select sum(pallet_count) from uchoice_storage where warehouse_code = any(:w)"),
                       {"w": list(WAREHOUSES)}).scalar()
    assert stock == 12

    assert relabel.main(["--database-url", _url(), "--apply"], expected=golive) == 0
    db.rollback()
    assert _types(db)[0] == ("RLTEST1", "opening", 3, 9)


def test_count_mismatch_changes_nothing(db, golive):
    before = _types(db)
    wrong = {**golive, "RLTEST2": (1, 5)}
    assert relabel.main(["--database-url", _url(), "--apply"], expected=wrong) == 1
    db.rollback()
    assert _types(db) == before


def test_earliest_change_not_a_recount_request_changes_nothing(db, golive):
    inbound = _request(db, "confirm_inbound_completion")
    _txn(db, "RLTEST2", "s5", 50, 1, "inbound", datetime(2031, 8, 1, tzinfo=timezone.utc), inbound)
    before = _types(db)
    assert relabel.main(["--database-url", _url(), "--apply"], expected=golive) == 1
    db.rollback()
    assert _types(db) == before
