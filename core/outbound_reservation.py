"""
Scheduled-outbound reservation -- derived, never stored.

A confirmed outbound request (request_log.status = 'processing') leaves
uchoice_storage untouched until the warehouseman completes it, so physical
storage alone can't say what is still free to schedule. This module derives
that: available = physical - boxes in confirmed, uncompleted outbound
requests, per (warehouse, SKU). Completion and cancellation move the log out
of 'processing', so a reservation releases itself.

Reservation is counted in boxes per SKU on both channels. Only 'processing'
requests count: drafts awaiting confirmation (including outbounds parked for
a new address, which stay 'pending') hold nothing, which is why the confirm
step re-checks under the storage lock (locked_stock_shortages).

Completion paths keep checking physical stock: a scheduled request must be
able to complete against the stock it reserved.

Plan: docs/ai-collaboration/2026-10-scheduled-outbound-reservation/plan.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

OUTBOUND_SERVICE = "uchoice_outbound_request"


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def requested_boxes_by_sku(fields: dict) -> dict[str, int]:
    """Per-SKU box totals of a request's sku_lines, loose and palletized
    together. Lines that aren't fully specified yet are skipped."""
    required: dict[str, int] = {}
    for line in (fields or {}).get("sku_lines") or []:
        if not isinstance(line, dict) or not line.get("sku_code"):
            continue
        if line.get("box_count") is not None:
            boxes = line["box_count"]
        else:
            bpp, pallets = line.get("boxes_per_pallet"), line.get("pallet_count")
            if not (_is_positive_int(bpp) and _is_positive_int(pallets)):
                continue
            boxes = bpp * pallets
        if not _is_positive_int(boxes):
            continue
        required[line["sku_code"]] = required.get(line["sku_code"], 0) + boxes
    return required


def scheduled_targets(db: DBSession, warehouse_code: str | None = None, exclude_log_id=None) -> list:
    """Confirmed, uncompleted outbound requests (oldest first) as
    core.completion_batch.BatchTarget, optionally for one source warehouse."""
    from models.request_log import RequestLog
    from models.service import ServiceType
    from core.completion_batch import load_target

    rows = (
        db.query(RequestLog.log_id, RequestLog.serial_number)
        .join(ServiceType, ServiceType.service_type_id == RequestLog.service_type_id)
        .filter(ServiceType.name == OUTBOUND_SERVICE, RequestLog.status == "processing")
        .order_by(RequestLog.created_at.asc(), RequestLog.serial_number.asc())
        .all()
    )
    targets = []
    for log_id, serial in rows:
        if exclude_log_id is not None and str(log_id) == str(exclude_log_id):
            continue
        target = load_target(db, serial)
        if target is None or (warehouse_code and target.warehouse_code != warehouse_code):
            continue
        targets.append(target)
    return targets


def scheduled_boxes(db: DBSession, warehouse_code: str, exclude_log_id=None) -> dict[str, int]:
    """SKU -> boxes reserved by confirmed outbounds drawing from this warehouse."""
    totals: dict[str, int] = {}
    for target in scheduled_targets(db, warehouse_code, exclude_log_id):
        for sku, boxes in requested_boxes_by_sku({"sku_lines": target.sku_lines()}).items():
            totals[sku] = totals.get(sku, 0) + boxes
    return totals


@dataclass(frozen=True)
class Shortage:
    sku_code: str
    requested_boxes: int
    physical_boxes: int
    scheduled_boxes: int

    @property
    def available_boxes(self) -> int:
        return max(0, self.physical_boxes - self.scheduled_boxes)


def stock_shortages(db: DBSession, fields: dict, exclude_log_id=None) -> list[Shortage]:
    """SKUs whose requested boxes exceed available (physical - scheduled)."""
    from core import uchoice_storage

    warehouse = (fields or {}).get("warehouse_code")
    required = requested_boxes_by_sku(fields)
    if not warehouse or not required:
        return []
    scheduled = scheduled_boxes(db, warehouse, exclude_log_id)
    shortages = []
    for sku, requested in sorted(required.items()):
        physical = uchoice_storage.available_box_count(db, warehouse, sku)
        if max(0, physical - scheduled.get(sku, 0)) < requested:
            shortages.append(Shortage(sku, requested, physical, scheduled.get(sku, 0)))
    return shortages


def locked_stock_shortages(db: DBSession, fields: dict, exclude_log_id=None) -> list[Shortage]:
    """The confirm-time check: same rule as stock_shortages, but taking the
    per-(warehouse, SKU) storage lock first so two concurrent confirms of the
    same SKU are serialized and the second sees the first's reservation.
    Must run in the transaction that moves the log to 'processing'."""
    from core.uchoice_storage import acquire_storage_scopes

    warehouse = (fields or {}).get("warehouse_code")
    required = requested_boxes_by_sku(fields)
    if not warehouse or not required:
        return []
    rows = acquire_storage_scopes(db, [(warehouse, sku) for sku in required])
    # acquire_storage_scopes' query returns identity-mapped objects as they
    # were loaded earlier in this session. Refresh the now-locked rows so the
    # check sees what other transactions committed while we waited.
    for row in rows:
        db.refresh(row)
    physical_by_sku: dict[str, int] = {}
    for row in rows:
        physical_by_sku[row.sku_code] = physical_by_sku.get(row.sku_code, 0) + max(0, row.pallet_count) * row.boxes_per_pallet
    scheduled = scheduled_boxes(db, warehouse, exclude_log_id)
    shortages = []
    for sku, requested in sorted(required.items()):
        physical = physical_by_sku.get(sku, 0)
        if max(0, physical - scheduled.get(sku, 0)) < requested:
            shortages.append(Shortage(sku, requested, physical, scheduled.get(sku, 0)))
    return shortages


def shortage_text(sku_label: str, s: Shortage) -> str:
    """One shortage as a line fragment, naming the held stock when there is any."""
    if s.scheduled_boxes <= 0:
        return f"{sku_label}：申请 {s.requested_boxes} 箱，现有 {s.physical_boxes} 箱"
    return (
        f"{sku_label}：申请 {s.requested_boxes} 箱，可用 {s.available_boxes} 箱"
        f"（现有 {s.physical_boxes} 箱，其中 {min(s.scheduled_boxes, s.physical_boxes)} 箱已被其他待出库申请占用）"
    )


# ── Display ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SchedulePosition:
    """What is scheduled out of one (warehouse, SKU), for display."""
    scheduled_pallets: int      # palletized lines, as requested
    scheduled_loose_boxes: int  # loose (box_count) lines, as requested
    scheduled_boxes: int
    physical_boxes: int
    remaining_pallets: int      # pallets left after every scheduled outbound ships (an estimate)

    @property
    def shortfall_boxes(self) -> int:
        return max(0, self.scheduled_boxes - self.physical_boxes)


def schedule_line(pos: SchedulePosition) -> str:
    """The single extra line shown under a SKU that has scheduled outbound."""
    if pos.shortfall_boxes:
        return f"⚠ 现有 {pos.physical_boxes} 箱，待出库 {pos.scheduled_boxes} 箱，缺口 {pos.shortfall_boxes} 箱"
    parts = []
    if pos.scheduled_pallets:
        parts.append(f"{pos.scheduled_pallets} 托")
    if pos.scheduled_loose_boxes:
        parts.append(f"{pos.scheduled_loose_boxes} 箱（散）")
    return f"待出库 {' + '.join(parts)}，出库后剩 {pos.remaining_pallets} 托"


def schedule_positions(db: DBSession, warehouse_code: str) -> dict[str, SchedulePosition]:
    """SKU -> SchedulePosition for one warehouse; only SKUs with scheduled
    outbound appear. Display only: any failure yields {} rather than breaking
    the reply it decorates."""
    try:
        return _schedule_positions(db, warehouse_code)
    except Exception:
        logger.exception("schedule_positions failed for %s", warehouse_code)
        return {}


def _schedule_positions(db: DBSession, warehouse_code: str) -> dict[str, SchedulePosition]:
    from core.completion_batch import load_buckets, simulate_allocation

    targets = scheduled_targets(db, warehouse_code)
    if not targets:
        return {}
    pallets: dict[str, int] = {}
    loose: dict[str, int] = {}
    for target in targets:
        for line in target.sku_lines():
            sku = line.get("sku_code")
            if not sku:
                continue
            if line.get("box_count") is not None:
                loose[sku] = loose.get(sku, 0) + line["box_count"]
            elif _is_positive_int(line.get("pallet_count")):
                pallets[sku] = pallets.get(sku, 0) + line["pallet_count"]
    skus = sorted(set(pallets) | set(loose))
    scopes = [(warehouse_code, sku) for sku in skus]
    buckets = load_buckets(db, scopes)
    # Source side only: a transfer's destination credit is incoming stock, not
    # something this warehouse can offer yet.
    sim = simulate_allocation(buckets, [replace(t, destination_warehouse_code=None) for t in targets])
    scheduled = scheduled_boxes(db, warehouse_code)
    out: dict[str, SchedulePosition] = {}
    for sku in skus:
        physical = sum(max(0, c) * bpp for bpp, c in buckets[(warehouse_code, sku)].items())
        remaining = sum(max(0, c) for c in sim.final_buckets.get((warehouse_code, sku), {}).values())
        out[sku] = SchedulePosition(
            scheduled_pallets=pallets.get(sku, 0),
            scheduled_loose_boxes=loose.get(sku, 0),
            scheduled_boxes=scheduled.get(sku, 0),
            physical_boxes=physical,
            remaining_pallets=remaining,
        )
    return out
