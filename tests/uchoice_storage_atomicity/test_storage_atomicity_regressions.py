"""
Regression coverage for atomic multi-delta storage operations.

Uses the real dev Postgres DB directly -- SQLite (used by
tests/uchoice_lifecycle/'s mocked suite) can't reproduce real multi-
statement commit/rollback semantics, which is exactly what's under test
here. A throwaway warehouse code (TESTWHX2) isolates fixtures; only exact
rows this module creates are ever deleted.

The assertions protect against partial commits surviving a later failure.
"""
import pytest
from sqlalchemy import text

from database import SessionLocal
import models.request_log  # noqa: F401 -- registers RequestLog for UchoiceStorageTxn's FK before any query
from handlers.uchoice.storage_txns import MoveStorageHandler
from core.uchoice_storage import apply_storage_delta

WH = "TESTWHX2"
SKU = "s1"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    # A test that hit a RuntimeError mid-transaction (the whole point of the
    # atomicity tests) can leave this session in a failed-transaction state;
    # roll back first so cleanup itself doesn't silently no-op.
    db.rollback()
    db.execute(text("delete from uchoice_storage_txn where warehouse_code = :wh"), {"wh": WH})
    db.execute(text("delete from uchoice_storage where warehouse_code = :wh"), {"wh": WH})
    db.commit()


def _seed(db, *, source_bpp, source_pallets, target_bpp, target_pallets):
    db.execute(text(
        "insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count) "
        "values (:wh, :sku, :bpp, :n)"
    ), {"wh": WH, "sku": SKU, "bpp": source_bpp, "n": source_pallets})
    if target_pallets is not None:
        db.execute(text(
            "insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count) "
            "values (:wh, :sku, :bpp, :n)"
        ), {"wh": WH, "sku": SKU, "bpp": target_bpp, "n": target_pallets})
    db.commit()


def _bucket_pallets(db, bpp):
    return db.execute(text(
        "select pallet_count from uchoice_storage where warehouse_code = :wh and sku_code = :sku and boxes_per_pallet = :bpp"
    ), {"wh": WH, "sku": SKU, "bpp": bpp}).scalar()


def test_move_storage_partial_failure_leaves_no_partial_writes(db):
    # source has 5 pallets @ 80/tray; moving 10 boxes leaves 70/tray (new bucket).
    # target bucket is a genuinely distinct bucket (64/tray) with ZERO pallets --
    # its decrement (call 3, move_out target_bpp=64) must fail on insufficient stock.
    _seed(db, source_bpp=80, source_pallets=5, target_bpp=64, target_pallets=0)

    context = {
        "collected_fields": {
            "warehouse_code": WH,
            "move_lines": [{
                "sku_code": SKU,
                "source_boxes_per_pallet": 80,
                "box_count_moved": 10,
                "target_boxes_per_pallet": 64,
            }],
        },
        "request_log_id": None,
        "wechat_openid": "atomicity_test",
    }

    with pytest.raises(RuntimeError):
        MoveStorageHandler().handle(context, {}, db)

    # Mirrors what core.workflow_engine._execute_workflow_and_finish now does
    # on a DB-phase failure: roll back the whole transaction before checking
    # anything. Without the atomicity fix, calls 1-2 already committed
    # themselves individually (apply_storage_delta's old per-call db.commit())
    # -- a rollback here can't undo an already-committed transaction, so the
    # bug still shows up as "changes survive" even after this rollback.
    db.rollback()

    # correct (post-fix) behavior: the failed operation leaves the source
    # bucket untouched -- calls 1-2 (source_bpp -1, new_source_bpp +1) must
    # not have survived just because call 3 (target_bpp -1) failed.
    assert _bucket_pallets(db, 80) == 5, "source bucket should be unchanged after a rolled-back move"
    assert _bucket_pallets(db, 70) is None, "the new-source-bpp bucket should never have been created"


def test_move_storage_success_case_still_works(db):
    """Control case: a fully valid move must still work exactly as before."""
    _seed(db, source_bpp=80, source_pallets=5, target_bpp=64, target_pallets=3)

    context = {
        "collected_fields": {
            "warehouse_code": WH,
            "move_lines": [{
                "sku_code": SKU,
                "source_boxes_per_pallet": 80,
                "box_count_moved": 10,
                "target_boxes_per_pallet": 64,
            }],
        },
        "request_log_id": None,
        "wechat_openid": "atomicity_test",
    }

    result = MoveStorageHandler().handle(context, {}, db)
    assert result["move_lines"][0]["sku_code"] == SKU
    assert _bucket_pallets(db, 80) == 4  # 5 - 1
    assert _bucket_pallets(db, 70) == 1  # new bucket, +1
    assert _bucket_pallets(db, 64) == 2  # 3 - 1
    assert _bucket_pallets(db, 74) == 1  # new bucket, +1
