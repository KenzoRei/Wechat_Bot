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
Q4  Deploy gate: Kefu invoice / storage-history files still waiting to
    send that were queued without stored bytes (by code before V37).
    The new code would rebuild them in the new layout, which can never
    match the hash recorded at queue time, so each would fail. Must be 0
    right before deploying the invoice-inventory release; if not, wait
    for them to send and re-run. Skipped before V37 is applied.
Q5  After deploying (informational): invoice / storage-history file
    deliveries that failed on artifact_hash_mismatch in the last 24 hours
    -- a file the old code queued in the moment between the Q4 check and
    the switchover. The report itself is read-only: the requester just
    asks for it again.

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
Exit code: 0 = Q1 clean and Q4 is 0, 1 = Q1 or Q4 found rows,
2 = usage/connection error.
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


Q4 = """
SELECT d.artifact_doc_type, d.idempotency_key, d.attempt_count, d.next_retry_at, d.last_error, d.created_at
FROM kefu_outbound_delivery d
WHERE d.status = 'pending' AND d.payload_type = 'file'
  AND d.artifact_doc_type IN ('invoice_workbook', 'storage_history_workbook')
  AND NOT EXISTS (SELECT 1 FROM kefu_artifact_blob b WHERE b.artifact_key = d.artifact_key)
ORDER BY d.created_at
"""


Q5 = """
SELECT d.artifact_doc_type, d.idempotency_key, d.recipient_staff_id, d.updated_at
FROM kefu_outbound_delivery d
WHERE d.status = 'failed' AND d.payload_type = 'file'
  AND d.artifact_doc_type IN ('invoice_workbook', 'storage_history_workbook')
  AND d.last_error LIKE '%artifact_hash_mismatch%'
  AND d.updated_at > now() - interval '24 hours'
ORDER BY d.updated_at
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
            print()

            q4 = []
            print("Q4  Kefu files queued without stored bytes, still pending (deploy gate: expect 0 rows)")
            cur.execute("SELECT to_regclass('public.kefu_artifact_blob') IS NOT NULL")
            if not cur.fetchone()[0]:
                print("  (skipped: V37 not applied yet)")
            else:
                cur.execute(Q4)
                q4 = cur.fetchall()
                if q4:
                    _print_table(["doc type", "idempotency key", "attempts", "next retry", "last error", "queued"], q4)
                    print(f"  -> {len(q4)} pending. Do NOT deploy yet: wait for them to send, then re-run.")
                else:
                    print("  -> 0 rows. Safe to deploy.")

            print()
            print("Q5  File deliveries failed on hash mismatch, last 24h (after deploy, informational)")
            cur.execute(Q5)
            q5 = cur.fetchall()
            if q5:
                _print_table(["doc type", "idempotency key", "staff", "failed at"], q5)
                print(f"  -> {len(q5)} failed. Ask those staff to request the report again.")
            else:
                print("  -> 0 rows.")
    finally:
        conn.rollback()
        conn.close()

    return 1 if (q1 or q4) else 0


if __name__ == "__main__":
    sys.exit(main())
