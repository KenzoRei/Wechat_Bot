import pytest
from sqlalchemy import text

from database import SessionLocal
from handlers.uchoice.storage_txns import ApplyOutboundStorageHandler


WAREHOUSE = "KFBXTEST"
DESTINATION = "KFBXDEST"
SKU = "s2"


def _cleanup(db):
    db.execute(text("delete from uchoice_storage_txn where warehouse_code in (:origin,:destination)"), {
        "origin": WAREHOUSE, "destination": DESTINATION,
    })
    db.execute(text("delete from uchoice_storage where warehouse_code in (:origin,:destination)"), {
        "origin": WAREHOUSE, "destination": DESTINATION,
    })
    db.commit()


def test_palletized_final_packing_consumes_boxes_across_source_buckets():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:wh,:sku,40,2),(:wh,:sku,80,1)"
        ), {"wh": WAREHOUSE, "sku": SKU})
        db.commit()

        result = ApplyOutboundStorageHandler().handle({
            "source_channel": "kefu",
            "request_log_id": None,
            "wechat_openid": "test-kefu",
            "collected_fields": {},
            "_uchoice_target": {
                "warehouse_code": WAREHOUSE,
                "original_fields": {
                    "sku_lines": [{"sku_code": SKU, "boxes_per_pallet": 72, "pallet_count": 2}],
                },
            },
        }, {}, db)
        db.commit()

        rows = db.execute(text(
            "select boxes_per_pallet,pallet_count from uchoice_storage "
            "where warehouse_code=:wh and sku_code=:sku and pallet_count<>0 order by boxes_per_pallet"
        ), {"wh": WAREHOUSE, "sku": SKU}).all()
        assert rows == [(16, 1)]
        assert result["source_picks"] == [{
            "sku_code": SKU,
            "picks": [
                {"source_boxes_per_pallet": 40, "box_count": 80},
                {"source_boxes_per_pallet": 80, "box_count": 64},
            ],
        }]
        assert result["fulfillment_lines"][0]["boxes_per_pallet"] == 72
    finally:
        db.rollback()
        _cleanup(db)
        db.close()


def test_stock_conflict_returns_marker_without_partial_mutation():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:wh,:sku,40,1),(:wh,:sku,80,1)"
        ), {"wh": WAREHOUSE, "sku": SKU})
        db.commit()

        result = ApplyOutboundStorageHandler().handle({
            "source_channel": "kefu",
            "request_log_id": None,
            "wechat_openid": "test-kefu",
            "collected_fields": {},
            "_uchoice_target": {
                "warehouse_code": WAREHOUSE,
                "original_fields": {
                    "sku_lines": [{"sku_code": SKU, "boxes_per_pallet": 72, "pallet_count": 2}],
                },
            },
        }, {}, db)

        assert result["_kefu_stop_workflow"] == "stock_changed"
        assert result["stock_shortages"] == [{
            "sku_code": SKU, "requested_boxes": 144, "available_boxes": 120,
        }]
        assert db.execute(text(
            "select sum(boxes_per_pallet*pallet_count) from uchoice_storage "
            "where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WAREHOUSE, "sku": SKU}).scalar() == 120
        assert db.execute(text(
            "select count(*) from uchoice_storage_txn where warehouse_code=:wh"
        ), {"wh": WAREHOUSE}).scalar() == 0
    finally:
        db.rollback()
        _cleanup(db)
        db.close()


def test_loose_internal_transfer_requires_destination_packing():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:wh,:sku,80,2)"
        ), {"wh": WAREHOUSE, "sku": SKU})
        db.commit()

        with pytest.raises(RuntimeError):
            ApplyOutboundStorageHandler()._handle_kefu_box_level(
                context={"collected_fields": {}}, db=db,
                fulfillment_lines=[{"sku_code": SKU, "box_count": 100}],
                warehouse_code=WAREHOUSE, destination_warehouse_code=DESTINATION,
                request_log_id=None, created_by="test-kefu",
                transportation_fee=0, transfer_note="test transfer", original_fields={},
            )

        assert db.execute(text(
            "select sum(boxes_per_pallet*pallet_count) from uchoice_storage "
            "where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WAREHOUSE, "sku": SKU}).scalar() == 160
    finally:
        db.rollback()
        _cleanup(db)
        db.close()


def test_internal_transfer_uses_final_destination_packing_and_conserves_boxes():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values (:wh,:sku,40,2),(:wh,:sku,80,1)"
        ), {"wh": WAREHOUSE, "sku": SKU})
        db.commit()

        result = ApplyOutboundStorageHandler()._handle_kefu_box_level(
            context={"collected_fields": {
                "destination_packing_lines": [
                    {"sku_code": SKU, "boxes_per_pallet": 72, "pallet_count": 2},
                ],
            }},
            db=db,
            fulfillment_lines=[{"sku_code": SKU, "box_count": 144}],
            warehouse_code=WAREHOUSE, destination_warehouse_code=DESTINATION,
            request_log_id=None, created_by="test-kefu",
            transportation_fee=0, transfer_note="test transfer", original_fields={},
        )
        db.commit()

        totals = dict(db.execute(text(
            "select warehouse_code,sum(boxes_per_pallet*pallet_count) "
            "from uchoice_storage where warehouse_code in (:origin,:destination) and sku_code=:sku "
            "group by warehouse_code"
        ), {"origin": WAREHOUSE, "destination": DESTINATION, "sku": SKU}).all())
        assert totals == {WAREHOUSE: 16, DESTINATION: 144}
        assert result["destination_packing_lines"] == [
            {"sku_code": SKU, "boxes_per_pallet": 72, "pallet_count": 2},
        ]
    finally:
        db.rollback()
        _cleanup(db)
        db.close()
