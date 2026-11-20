"""One-off: re-price stored outbound transportation fees per pallet (v1.10.3).

Before v1.10.3 an outbound's transportation_fee was the flat per-pallet rate.
Invoices read the stored value from request_log.result, so old logs keep the
flat fee until rewritten. This script rewrites them as rate x pallets, using
only what the log itself stores (old fee == the rate).

Pallets (same rule as ApplyOutboundStorageHandler._shipped_pallets):
  - kefu transfer: sum of result.destination_packing_lines[].pallet_count
  - otherwise: whole-pallet lines' pallet_count + palletization_fee / 15
    (= new_pallet_count); at least 1 if anything shipped.

Dry run by default. --before is REQUIRED: only logs completed before the
v1.10.3 deploy are touched, so new-format logs are never multiplied twice.
Applied rows get result.transportation_pallets + transportation_fee_flat_old,
which also makes re-runs skip them.

  python scripts/backfill_transportation_fee.py --before 2026-11-05T12:00:00
  python scripts/backfill_transportation_fee.py --before ... --apply
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm.attributes import flag_modified

from core.uchoice_rates import PALLETIZATION_PER_PALLET
from database import SessionLocal
from models.request_log import RequestLog
from models.service import ServiceType


def pallets_for(result: dict) -> int:
    packing = result.get("destination_packing_lines")
    if packing:
        return sum(int(l.get("pallet_count") or 0) for l in packing)
    lines = result.get("fulfillment_lines") or []
    whole = sum(
        int(l["pallet_count"]) for l in lines
        if "picks" not in l and l.get("box_count") is None
        and isinstance(l.get("pallet_count"), int)
    )
    new_pallets = round(float(result.get("palletization_fee") or 0) / PALLETIZATION_PER_PALLET)
    total = whole + new_pallets
    return total if total > 0 else (1 if lines else 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="ISO time of the v1.10.3 deploy")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cutoff = datetime.fromisoformat(args.before)

    db = SessionLocal()
    try:
        st = db.query(ServiceType).filter_by(name="uchoice_outbound_request").first()
        logs = db.query(RequestLog).filter(
            RequestLog.service_type_id == st.service_type_id,
            RequestLog.status == "success",
            RequestLog.completed_at < cutoff,
        ).order_by(RequestLog.completed_at).all()

        changed = 0
        for log in logs:
            res = log.result or {}
            old = res.get("transportation_fee") or 0
            if not old or "transportation_pallets" in res:
                continue
            n = pallets_for(res)
            new = old * n
            print(f"{log.serial_number}  {log.completed_at:%Y-%m-%d}  "
                  f"wh={res.get('warehouse_code')}  ${old} x {n} plt -> ${new}"
                  f"{'' if new != old else '  (unchanged)'}")
            if args.apply and new != old:
                res["transportation_fee_flat_old"] = old
                res["transportation_pallets"] = n
                res["transportation_fee"] = new
                log.result = res
                flag_modified(log, "result")
                changed += 1
        if args.apply:
            db.commit()
            print(f"\napplied: {changed} log(s) updated")
        else:
            print("\ndry run -- nothing written (add --apply)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
