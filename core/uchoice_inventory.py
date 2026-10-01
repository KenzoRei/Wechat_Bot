"""
Per-SKU opening/closing pallet balances for an invoice period, derived from
the stock-change history (uchoice_storage_txn) -- no snapshot table needed:
every stock write goes through core.uchoice_storage.apply_storage_delta,
which always records a change row (production check 2026-10-01: history
matches current stock exactly).

Invoice-inventory plan, Phase 2 (docs/ai-collaboration/
2026-10-invoice-inventory/plan.md). One row per (warehouse, SKU); all
quantities are pallets:

- opening  = changes before the period, plus 'opening' changes inside it
             (the go-live load is tagged 'opening', D10, so the first month
             opens with it instead of showing it as a movement)
- inbound  = 'inbound' changes inside the period
- outbound = 'outbound' changes inside the period (whole pallets that left)
- other    = every other change inside the period (transfer, move, convert,
             adjust, recount)
- closing  = opening + inbound + outbound + other

A loose-box pick is recorded as a repackaging pair (-1 pallet at the old
size, +1 at the smaller size), so per SKU it nets to 0 pallets: no pallet
movement. It shows only in closing_mix, the closing balance per pallet size.

Period boundaries are the same half-open [start, end_exclusive) dates the
invoice fee sheets use (core.uchoice_invoice._resolve_range), so this sheet
always agrees with them.
"""
from datetime import date

from sqlalchemy import case, func
from sqlalchemy.orm import Session as DBSession

from models.uchoice import UchoiceStorageTxn

_MOVEMENT_TYPES = ("inbound", "outbound")
_OPENING_TYPE = "opening"


def inventory_balances(db: DBSession, warehouse_codes: list[str], start: date, end_exclusive: date) -> list[dict]:
    """
    Returns one dict per (warehouse_code, sku_code), sorted by both:
    {warehouse_code, sku_code, opening, inbound, outbound, other, closing,
    closing_mix: [(boxes_per_pallet, pallets), ...] ascending, non-zero only}.
    A SKU is included if it has a non-zero opening or closing balance, or any
    change inside the period.
    """
    t = UchoiceStorageTxn
    delta = t.pallet_delta
    before = t.created_at < start
    in_period = t.created_at >= start

    def _sum(condition):
        return func.coalesce(func.sum(case((condition, delta), else_=0)), 0)

    rows = (
        db.query(
            t.warehouse_code,
            t.sku_code,
            t.boxes_per_pallet,
            _sum(before | (in_period & (t.txn_type == _OPENING_TYPE))).label("opening"),
            _sum(in_period & (t.txn_type == "inbound")).label("inbound"),
            _sum(in_period & (t.txn_type == "outbound")).label("outbound"),
            _sum(in_period & t.txn_type.notin_(_MOVEMENT_TYPES + (_OPENING_TYPE,))).label("other"),
            func.coalesce(func.sum(delta), 0).label("closing"),
            func.count(case((in_period, 1))).label("changes_in_period"),
        )
        .filter(t.warehouse_code.in_(list(warehouse_codes)), t.created_at < end_exclusive)
        .group_by(t.warehouse_code, t.sku_code, t.boxes_per_pallet)
        .all()
    )

    by_sku: dict[tuple[str, str], dict] = {}
    for r in rows:
        entry = by_sku.setdefault((r.warehouse_code, r.sku_code), {
            "warehouse_code": r.warehouse_code, "sku_code": r.sku_code,
            "opening": 0, "inbound": 0, "outbound": 0, "other": 0, "closing": 0,
            "closing_mix": [], "_changes": 0,
        })
        for key in ("opening", "inbound", "outbound", "other", "closing"):
            entry[key] += int(getattr(r, key))
        entry["_changes"] += int(r.changes_in_period)
        if r.closing:
            entry["closing_mix"].append((r.boxes_per_pallet, int(r.closing)))

    result = []
    for key in sorted(by_sku):
        entry = by_sku[key]
        changes = entry.pop("_changes")
        if not (entry["opening"] or entry["closing"] or changes):
            continue
        entry["closing_mix"].sort()
        result.append(entry)
    return result


def format_closing_mix(mix: list[tuple[int, int]]) -> str:
    """One pallet size per line, same notation and order as the 库存查询 reply."""
    return "\n".join(f"{pallets} 托 @ {bpp}/托" for bpp, pallets in mix)
