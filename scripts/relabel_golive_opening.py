"""
One-time data update (invoice-inventory plan, Phase 2b, decision D10):
retag each warehouse's go-live stock load from 'recount' to 'opening', so the
invoice Inventory sheet opens September with that stock instead of showing
it as a September movement.

What changes: txn_type ('recount' -> 'opening') and note (appends
"go-live opening stock (was recount)"). Nothing else: pallet_delta,
created_at, bucket, request_log_id and current stock (uchoice_storage) are
untouched, so every balance and the history-vs-stock check (Q1) are
unchanged.

Selection: for each warehouse, the request behind its earliest stock change
(the go-live 库存盘点 / recount_storage request), and exactly that request's
rows. Hard checks -- the script changes nothing unless ALL hold:
  - the request is a recount_storage request, and all its rows are in that
    warehouse and tagged 'recount'
  - per warehouse, row count and pallet sum equal the expected values
    (production inspection 2026-10-01: DE 5/42, JFK 18/51, NJ 5/23)
  - migration V36 is applied (the 'opening' type is allowed)
After updating, Q1 is re-run inside the same transaction; any difference
rolls everything back.

Idempotent: a warehouse whose go-live rows are already 'opening' is reported
and skipped.

Usage (dry run by default; --apply to write):
    python scripts/relabel_golive_opening.py [--database-url URL] [--apply]
Render shell, if this file isn't deployed yet: paste it as
    python - --apply <<'EOF' ... EOF        (omit --apply for the dry run)
Exit code: 0 = ok (dry run clean or applied), 1 = a check failed, nothing
changed, 2 = usage/connection error.
"""
import argparse
import os
import sys

import psycopg2

# warehouse -> (rows, pallet sum) of the go-live recount, from the
# production inspection query run 2026-10-01.
EXPECTED = {"DE": (5, 42), "JFK": (18, 51), "NJ": (5, 23)}
NOTE = "go-live opening stock (was recount)"

_Q1 = """
WITH txn AS (
  SELECT warehouse_code, sku_code, boxes_per_pallet, SUM(pallet_delta) AS txn_sum
  FROM uchoice_storage_txn GROUP BY 1, 2, 3
)
SELECT COUNT(*)
FROM uchoice_storage s
FULL OUTER JOIN txn t USING (warehouse_code, sku_code, boxes_per_pallet)
WHERE COALESCE(s.pallet_count, 0) <> COALESCE(t.txn_sum, 0)
"""


class CheckFailed(Exception):
    pass


def plan(cur, expected: dict) -> list[dict]:
    """Find and verify each warehouse's go-live rows. Raises CheckFailed."""
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'uchoice_storage_txn_txn_type_check'")
    row = cur.fetchone()
    if row is None or "'opening'" not in row[0]:
        raise CheckFailed("migration V36 is not applied: the 'opening' stock-change type isn't allowed yet")

    batches = []
    for warehouse, (want_rows, want_pallets) in sorted(expected.items()):
        cur.execute("""
            SELECT request_log_id FROM uchoice_storage_txn
            WHERE warehouse_code = %s ORDER BY created_at, txn_id LIMIT 1""", (warehouse,))
        first = cur.fetchone()
        if first is None or first[0] is None:
            raise CheckFailed(f"{warehouse}: earliest stock change has no request; expected the go-live 库存盘点")
        request_log_id = first[0]

        cur.execute("""
            SELECT rl.serial_number, st.name FROM request_log rl
            LEFT JOIN service_type st ON st.service_type_id = rl.service_type_id
            WHERE rl.log_id = %s""", (request_log_id,))
        req = cur.fetchone()
        if req is None or req[1] != "recount_storage":
            raise CheckFailed(f"{warehouse}: earliest change's request is {req[1] if req else 'missing'}, not recount_storage")
        serial = req[0]

        cur.execute("""
            SELECT txn_id, warehouse_code, sku_code, boxes_per_pallet, pallet_delta, txn_type, created_at
            FROM uchoice_storage_txn WHERE request_log_id = %s
            ORDER BY sku_code, boxes_per_pallet""", (request_log_id,))
        rows = cur.fetchall()
        types = {r[5] for r in rows}
        if any(r[1] != warehouse for r in rows):
            raise CheckFailed(f"{warehouse}: request {serial} also changed another warehouse's stock")
        if len(rows) != want_rows or sum(r[4] for r in rows) != want_pallets:
            raise CheckFailed(
                f"{warehouse}: request {serial} has {len(rows)} rows / {sum(r[4] for r in rows)} pallets, "
                f"expected {want_rows} / {want_pallets}")
        if types == {"opening"}:
            status = "already opening"
        elif types == {"recount"}:
            status = "to relabel"
        else:
            raise CheckFailed(f"{warehouse}: request {serial} has unexpected change types {sorted(types)}")
        batches.append({"warehouse": warehouse, "serial": serial, "rows": rows, "status": status})
    return batches


def apply(cur, batches: list[dict]) -> int:
    """Relabel the 'to relabel' batches; returns rows changed. Caller commits."""
    changed = 0
    for batch in batches:
        if batch["status"] != "to relabel":
            continue
        ids = [str(r[0]) for r in batch["rows"]]
        cur.execute("""
            UPDATE uchoice_storage_txn
            SET txn_type = 'opening',
                note = CASE WHEN note IS NULL OR note = '' THEN %s ELSE note || ' | ' || %s END
            WHERE txn_id = ANY(%s::uuid[]) AND txn_type = 'recount'""", (NOTE, NOTE, ids))
        if cur.rowcount != len(ids):
            raise CheckFailed(f"{batch['warehouse']}: updated {cur.rowcount} rows, expected {len(ids)}")
        changed += cur.rowcount
    cur.execute(_Q1)
    mismatches = cur.fetchone()[0]
    if mismatches:
        raise CheckFailed(f"history vs stock check (Q1) found {mismatches} difference(s) after relabelling")
    return changed


def main(argv=None, expected=None) -> int:
    parser = argparse.ArgumentParser(description="Relabel each warehouse's go-live recount as opening stock.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    expected = expected or EXPECTED

    if not args.database_url:
        print("No database URL: set DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(args.database_url)
    except psycopg2.Error as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 2

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            print(f"Database: {cur.fetchone()[0]}   mode: {'APPLY' if args.apply else 'dry run'}\n")
            batches = plan(cur, expected)
            for b in batches:
                pallets = sum(r[4] for r in b["rows"])
                print(f"{b['warehouse']}: request {b['serial']}  {len(b['rows'])} rows / {pallets} pallets  [{b['status']}]")
                for r in b["rows"]:
                    print(f"    {r[2]:<6} {r[3]:>4}/plt  {r[4]:>+4}  {r[5]:<8} {r[6]:%Y-%m-%d %H:%M} UTC")
            if not args.apply:
                print("\nDry run: nothing changed. Re-run with --apply to relabel.")
                conn.rollback()
                return 0
            changed = apply(cur, batches)
        conn.commit()
        print(f"\nApplied: {changed} row(s) relabelled 'recount' -> 'opening'. Q1 still 0 differences.")
        return 0
    except CheckFailed as exc:
        conn.rollback()
        print(f"\nCHECK FAILED, nothing changed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
