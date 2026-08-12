"""
Shared storage-mutation utility — adjust_storage, recount_storage,
move_storage, and both confirm_completion services all touch
uchoice_storage/uchoice_storage_txn. The actual mutation (write one txn row,
apply one delta) is written once here; each caller only differs in what
deltas it computes.
"""
from datetime import datetime, timezone
from collections.abc import Iterable
from dataclasses import dataclass
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session as DBSession
from models.uchoice import UchoiceStorage, UchoiceStorageTxn


@dataclass(frozen=True, order=True)
class StorageScope:
    warehouse_code: str
    sku_code: str


def _is_postgres(db: DBSession) -> bool:
    get_bind = getattr(db, "get_bind", None)
    if get_bind is None:
        return False
    bind = get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def acquire_storage_scopes(db: DBSession, scopes: Iterable[tuple[str, str] | StorageScope]) -> list[UchoiceStorage]:
    """Lock complete warehouse/SKU scopes in one global order.

    The transaction advisory lock serializes both existing rows and creation
    of a previously absent pallet bucket.  Callers touching more than one
    scope must declare the complete set up front; applying origin and then
    destination locks incrementally can deadlock opposing transfers.
    """
    normalized: set[StorageScope] = set()
    for scope in scopes:
        item = scope if isinstance(scope, StorageScope) else StorageScope(*scope)
        if item.warehouse_code and item.sku_code:
            normalized.add(item)
    ordered = sorted(normalized)
    if _is_postgres(db):
        for scope in ordered:
            key = f"uchoice-storage:{scope.warehouse_code}:{scope.sku_code}"
            db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})

    rows: list[UchoiceStorage] = []
    for scope in ordered:
        rows.extend(
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=scope.warehouse_code, sku_code=scope.sku_code)
            .order_by(UchoiceStorage.boxes_per_pallet.asc())
            .with_for_update()
            .all()
        )
    return rows


def available_box_count(db: DBSession, warehouse_code: str, sku_code: str, *, lock: bool = False) -> int:
    if lock:
        rows = acquire_storage_scopes(db, [(warehouse_code, sku_code)])
    else:
        rows = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=sku_code)
            .filter(UchoiceStorage.pallet_count > 0)
            .all()
        )
    return sum(max(0, row.pallet_count) * row.boxes_per_pallet for row in rows)


def allocate_box_picks(rows: Iterable[UchoiceStorage], box_count: int) -> list[dict] | None:
    """Allocate boxes from smallest pallet buckets first, without mutation."""
    if not isinstance(box_count, int) or isinstance(box_count, bool) or box_count <= 0:
        raise ValueError("box_count must be a positive integer")
    remaining = box_count
    picks: list[dict] = []
    for row in sorted(rows, key=lambda item: item.boxes_per_pallet):
        available = max(0, row.pallet_count) * row.boxes_per_pallet
        if not available:
            continue
        take = min(available, remaining)
        picks.append({"source_boxes_per_pallet": row.boxes_per_pallet, "box_count": take})
        remaining -= take
        if remaining == 0:
            return picks
    return None


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
    # Single-scope callers participate in the same protocol as multi-scope
    # handlers. Re-acquiring an already-held xact advisory lock is harmless.
    acquire_storage_scopes(db, [(warehouse_code, sku_code)])
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
    # No db.commit() here (Phase 2 atomicity fix, systemic-validation-addendum.md
    # Sec 3b) -- a multi-delta operation (move_storage's 4 calls/line, any
    # multi-line adjust/recount/completion) must succeed or fail as one unit.
    # Committing per-call let an earlier delta survive a later one's failure,
    # a real partial-write/inventory-loss risk. flush() still surfaces this
    # delta's own errors (e.g. the negative-balance check above) immediately,
    # without prematurely ending the caller's transaction -- the caller
    # (core/workflow_engine.py's DB phase) commits once, after every delta in
    # the operation has succeeded.
    db.flush()
    return bucket


def apply_loose_pick(
    db: DBSession,
    warehouse_code: str,
    sku_code: str,
    source_boxes_per_pallet: int,
    box_count: int,
    request_log_id,
    created_by: str,
    origin_txn_type: str = "outbound",
    destination_warehouse_code: str | None = None,
    transfer_note: str | None = None,
) -> None:
    """
    Ships box_count boxes of sku_code from a specific (warehouse_code,
    source_boxes_per_pallet) bucket. Whole pallets are decremented directly;
    a partial pallet is repackaged in place — the taken pallet is removed
    from the source bucket and the leftover boxes become a new, smaller
    bucket (boxes_per_pallet = source_boxes_per_pallet - remainder). The
    remainder is arithmetic, not something that needs to be separately
    reported by whoever picked the boxes.
    """
    full_pallets, remainder = divmod(box_count, source_boxes_per_pallet)

    if full_pallets:
        apply_storage_delta(
            db, warehouse_code, sku_code, source_boxes_per_pallet, -full_pallets,
            origin_txn_type, request_log_id, note=transfer_note, created_by=created_by
        )
        if destination_warehouse_code:
            apply_storage_delta(
                db, destination_warehouse_code, sku_code, source_boxes_per_pallet, full_pallets,
                "transfer_in", request_log_id, note=transfer_note, created_by=created_by
            )

    if remainder:
        apply_storage_delta(
            db, warehouse_code, sku_code, source_boxes_per_pallet, -1, "convert_out",
            request_log_id, note="loose-box pick", created_by=created_by
        )
        resulting_bpp = source_boxes_per_pallet - remainder
        apply_storage_delta(
            db, warehouse_code, sku_code, resulting_bpp, 1, "convert_in",
            request_log_id, note="loose-box pick", created_by=created_by
        )
        if destination_warehouse_code:
            apply_storage_delta(
                db, destination_warehouse_code, sku_code, remainder, 1, "transfer_in",
                request_log_id, note=transfer_note, created_by=created_by
            )
