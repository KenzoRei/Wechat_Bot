"""
Shared storage-mutation utility — adjust_storage, recount_storage,
move_storage, and both confirm_completion services all touch
uchoice_storage/uchoice_storage_txn. The actual mutation (write one txn row,
apply one delta) is written once here; each caller only differs in what
deltas it computes.
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession
from models.uchoice import UchoiceStorage, UchoiceStorageTxn


def apply_storage_delta(
    db: DBSession,
    warehouse_code: str,
    sku_code: str,
    boxes_per_pallet: int,
    delta: int,
    txn_type: str,
    request_log_id,
    note: str | None,
    created_by: str
) -> UchoiceStorage:
    """
    Upserts the (warehouse, sku, boxes_per_pallet) bucket by delta and writes
    one audit txn row. Raises RuntimeError with a clean message (not a raw DB
    constraint error) if the decrement would take the balance negative.
    """
    bucket = (
        db.query(UchoiceStorage)
        .filter_by(warehouse_code=warehouse_code, sku_code=sku_code, boxes_per_pallet=boxes_per_pallet)
        .with_for_update()
        .first()
    )
    if bucket is None:
        if delta < 0:
            raise RuntimeError(
                f"库存不足：{warehouse_code} {sku_code}@{boxes_per_pallet}/托 当前库存为 0，无法减少 {abs(delta)}"
            )
        bucket = UchoiceStorage(
            warehouse_code=warehouse_code,
            sku_code=sku_code,
            boxes_per_pallet=boxes_per_pallet,
            pallet_count=0,
        )
        db.add(bucket)
        db.flush()

    new_count = bucket.pallet_count + delta
    if new_count < 0:
        raise RuntimeError(
            f"库存不足：{warehouse_code} {sku_code}@{boxes_per_pallet}/托 当前库存 {bucket.pallet_count}，无法减少 {abs(delta)}"
        )

    bucket.pallet_count = new_count
    bucket.updated_at = datetime.now(timezone.utc)

    db.add(UchoiceStorageTxn(
        warehouse_code=warehouse_code,
        sku_code=sku_code,
        boxes_per_pallet=boxes_per_pallet,
        pallet_delta=delta,
        txn_type=txn_type,
        request_log_id=request_log_id,
        note=note,
        created_by=created_by,
    ))
    db.commit()
    return bucket
