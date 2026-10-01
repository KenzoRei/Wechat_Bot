"""
Phase 0 storage-history check (invoice inventory plan, Q1-Q3). READ-ONLY.

Q1  Does the stock-change history (uchoice_storage_txn) add up to current
    stock (uchoice_storage), per warehouse/SKU/pallet size? Compares both
    sides, so history-only buckets whose stock row is missing are caught too.
    Must return 0 rows before the Inventory sheet is released.
Q2  When does the history start, per warehouse? (informational)
Q3  Daily totals rebuilt from history vs the storage-fee ledger for one
    month (informational; small differences on busy days are expected
    because the daily job counts when it runs, not at end of day).

Self-contained: needs only psycopg2 (already an app dependency) and a
database URL. Runs inside a single READ ONLY transaction.

Render shell (DATABASE_URL is already set there):
    python scripts/check_storage_history.py
  or, if this file isn't deployed yet, paste it:
    python - <<'EOF'
    ...file contents...
    EOF

Local checkout against the External Database URL:
    python scripts/check_storage_history.py --database-url "<External Database URL>"

Options: --month YYYY-MM for Q3 (default 2026-09).
Exit code: 0 = Q1 clean, 1 = Q1 found differences, 2 = usage/connection error.
"""
import argparse
import calendar
import os
import sys

import psycopg2

Q1 = """
WITH txn AS (
  SELECT warehouse_code, sku_code, boxes_per_pallet, SUM(pallet_delta) AS txn_sum
  FROM uchoice_storage_txn GROUP BY 1, 2, 3
)
SELECT COALESCE(s.warehouse_code, t.warehouse_code),
       COALESCE(s.sku_code, t.sku_code),
       COALESCE(s.boxes_per_pallet, t.boxes_per_pallet),
       COALESCE(s.pallet_count, 0),
       COALESCE(t.txn_sum, 0),
       CASE WHEN s.warehouse_code IS NULL THEN 'history only (stock row missing)'
            WHEN t.warehouse_code IS NULL THEN 'stock only (no history)'
            ELSE 'mismatch' END
FROM uchoice_storage s
FULL OUTER JOIN txn t USING (warehouse_code, sku_code, boxes_per_pallet)
WHERE COALESCE(s.pallet_count, 0) <> COALESCE(t.txn_sum, 0)
ORDER BY 1, 2, 3
"""

Q2 = """
SELECT warehouse_code, MIN(created_at), MAX(created_at), COUNT(*)
FROM uchoice_storage_txn GROUP BY 1 ORDER BY 1
"""

Q3 = """
SELECT l.warehouse_code, l.fee_date, l.pallet_count,
       (SELECT COALESCE(SUM(t.pallet_delta), 0) FROM uchoice_storage_txn t
        WHERE t.warehouse_code = l.warehouse_code
          AND t.created_at < l.fee_date + 1)
FROM uchoice_storage_fee_ledger l
WHERE l.fee_date BETWEEN %s AND %s
ORDER BY 1, 2
"""


def _print_table(headers, rows):
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Phase 0 storage-history check.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--month", default="2026-09", help="Month for Q3, YYYY-MM (default 2026-09)")
    args = parser.parse_args()

    if not args.database_url:
        print("No database URL: set DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2
    try:
        year, month = (int(p) for p in args.month.split("-"))
        first = f"{year:04d}-{month:02d}-01"
        last = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    except ValueError:
        print("--month must be YYYY-MM", file=sys.stderr)
        return 2

    try:
        conn = psycopg2.connect(args.database_url)
    except psycopg2.Error as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 2
    conn.set_session(readonly=True)

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_setting('TimeZone')")
            dbname, tz = cur.fetchone()
            print(f"Database: {dbname}   session time zone: {tz}   (read-only)\n")

            cur.execute(Q1)
            q1 = cur.fetchall()
            print("Q1  Change history vs current stock (release gate: expect 0 rows)")
            if q1:
                _print_table(["warehouse", "sku", "boxes/plt", "stock", "history sum", "kind"], q1)
                print(f"  -> {len(q1)} row(s) differ. Do NOT release; confirm these rows before any backfill.\n")
            else:
                print("  -> 0 rows. History matches current stock.\n")

            cur.execute(Q2)
            print("Q2  History range per warehouse (informational)")
            _print_table(["warehouse", "first change", "last change", "changes"], cur.fetchall())
            print()

            cur.execute(Q3, (first, last))
            q3 = cur.fetchall()
            print(f"Q3  Fee-ledger daily pallets vs rebuilt from history, {args.month} (informational)")
            if q3:
                _print_table(
                    ["warehouse", "date", "ledger", "rebuilt", "diff"],
                    [(w, d, led, reb, reb - led) for w, d, led, reb in q3],
                )
                steady = {}
                for w, _d, led, reb in q3:
                    steady.setdefault(w, set()).add(reb - led)
                for w, diffs in sorted(steady.items()):
                    if len(diffs) == 1 and 0 not in diffs:
                        print(f"  -> {w}: the same non-zero difference every day ({diffs.pop()}); possible missing starting stock.")
            else:
                print("  (no ledger rows for this month)")
    finally:
        conn.rollback()
        conn.close()

    return 1 if q1 else 0


if __name__ == "__main__":
    sys.exit(main())
