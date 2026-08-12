import threading
import uuid

from sqlalchemy import text

from core.uchoice_storage import acquire_storage_scopes, apply_storage_delta
from database import SessionLocal
from handlers.uchoice.storage_txns import ApplyOutboundStorageHandler


def _cleanup(warehouses, sku):
    db = SessionLocal()
    try:
        db.execute(text(
            "delete from uchoice_storage_txn where warehouse_code=any(:warehouses) and sku_code=:sku"
        ), {"warehouses": warehouses, "sku": sku})
        db.execute(text(
            "delete from uchoice_storage where warehouse_code=any(:warehouses) and sku_code=:sku"
        ), {"warehouses": warehouses, "sku": sku})
        db.commit()
    finally:
        db.close()


def test_concurrent_creation_of_absent_bucket_is_serialized():
    warehouse = f"KC{uuid.uuid4().hex[:6]}"
    sku = "s2"
    barrier = threading.Barrier(2)
    errors = []

    def worker(actor):
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            apply_storage_delta(db, warehouse, sku, 72, 1, "inbound", None, None, actor)
            db.commit()
        except Exception as exc:  # captured and asserted in the parent thread
            db.rollback()
            errors.append(exc)
        finally:
            db.close()

    try:
        _cleanup([warehouse], sku)
        threads = [threading.Thread(target=worker, args=(f"actor-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []

        db = SessionLocal()
        try:
            assert db.execute(text(
                "select pallet_count from uchoice_storage "
                "where warehouse_code=:warehouse and sku_code=:sku and boxes_per_pallet=72"
            ), {"warehouse": warehouse, "sku": sku}).scalar_one() == 2
        finally:
            db.close()
    finally:
        _cleanup([warehouse], sku)


def test_opposing_transfers_use_one_global_scope_order_without_deadlock():
    warehouse_a = f"KA{uuid.uuid4().hex[:6]}"
    warehouse_b = f"KB{uuid.uuid4().hex[:6]}"
    sku = "s2"
    barrier = threading.Barrier(2)
    errors = []

    seed = SessionLocal()
    try:
        _cleanup([warehouse_a, warehouse_b], sku)
        seed.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:a,:sku,72,2),(:b,:sku,72,2)"
        ), {"a": warehouse_a, "b": warehouse_b, "sku": sku})
        seed.commit()
    finally:
        seed.close()

    def worker(origin, destination, actor):
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            acquire_storage_scopes(db, [(origin, sku), (destination, sku)])
            apply_storage_delta(db, origin, sku, 72, -1, "transfer_out", None, destination, actor)
            apply_storage_delta(db, destination, sku, 72, 1, "transfer_in", None, origin, actor)
            db.commit()
        except Exception as exc:
            db.rollback()
            errors.append(exc)
        finally:
            db.close()

    try:
        threads = [
            threading.Thread(target=worker, args=(warehouse_a, warehouse_b, "actor-a")),
            threading.Thread(target=worker, args=(warehouse_b, warehouse_a, "actor-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []

        db = SessionLocal()
        try:
            rows = dict(db.execute(text(
                "select warehouse_code,pallet_count from uchoice_storage "
                "where warehouse_code=any(:warehouses) and sku_code=:sku and boxes_per_pallet=72"
            ), {"warehouses": [warehouse_a, warehouse_b], "sku": sku}).all())
            assert rows == {warehouse_a: 2, warehouse_b: 2}
        finally:
            db.close()
    finally:
        _cleanup([warehouse_a, warehouse_b], sku)


def test_two_box_level_completions_yield_one_mutation_and_one_stock_change():
    warehouse = f"KF{uuid.uuid4().hex[:6]}"
    sku = "s2"
    barrier = threading.Barrier(2)
    errors = []
    results = []

    db = SessionLocal()
    try:
        _cleanup([warehouse], sku)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:warehouse,:sku,40,2),(:warehouse,:sku,80,1)"
        ), {"warehouse": warehouse, "sku": sku})
        db.commit()
    finally:
        db.close()

    def worker(actor):
        worker_db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = ApplyOutboundStorageHandler().handle({
                "source_channel": "kefu",
                "request_log_id": None,
                "wechat_openid": actor,
                "collected_fields": {},
                "_uchoice_target": {
                    "warehouse_code": warehouse,
                    "original_fields": {
                        "sku_lines": [{"sku_code": sku, "boxes_per_pallet": 72, "pallet_count": 2}],
                    },
                },
            }, {}, worker_db)
            worker_db.commit()
            results.append(result)
        except Exception as exc:
            worker_db.rollback()
            errors.append(exc)
        finally:
            worker_db.close()

    try:
        threads = [threading.Thread(target=worker, args=(f"actor-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert sum(result.get("_kefu_stop_workflow") == "stock_changed" for result in results) == 1

        verify = SessionLocal()
        try:
            assert verify.execute(text(
                "select sum(boxes_per_pallet*pallet_count) from uchoice_storage "
                "where warehouse_code=:warehouse and sku_code=:sku"
            ), {"warehouse": warehouse, "sku": sku}).scalar_one() == 16
        finally:
            verify.close()
    finally:
        _cleanup([warehouse], sku)
