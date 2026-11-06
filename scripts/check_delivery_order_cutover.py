"""
Delivery order PDF cutover check. READ-ONLY.

From this release the Kefu delivery order PDF ("outbound_instruction") is
stored byte-for-byte when it is queued (core.kefu_delivery
.STORED_ARTIFACT_DOC_TYPES) and read back for every retry, so a warehouse
edit or a later template change can never alter a queued file. A delivery
queued by the OLD code has no stored bytes and the old template's hash; the new
code would rebuild it with the new template, which can never match that hash,
so it would fail for good.

Q1  Deploy gate: pending delivery orders queued without stored bytes.
    Must be 0 right before deploying; if not, wait for them to send
    (they send when the customer's 48h window reopens) and re-run.
Q2  After deploying (informational): delivery orders that failed on
    artifact_hash_mismatch in the last 24 hours -- one queued by the old
    code in the moment between the Q1 check and the switchover. Send that
    staff member their delivery order again.

Render shell (DATABASE_URL is already set there):
    python scripts/check_delivery_order_cutover.py
Local checkout:
    python scripts/check_delivery_order_cutover.py --database-url "<External Database URL>"

Exit code: 0 = Q1 is 0, 1 = Q1 found rows, 2 = usage/connection error.
"""
import argparse
import os
import sys

import psycopg2

Q1 = """
SELECT d.idempotency_key, d.attempt_count, d.next_retry_at, d.last_error, d.created_at
FROM kefu_outbound_delivery d
WHERE d.status = 'pending' AND d.payload_type = 'file'
  AND d.artifact_doc_type = 'outbound_instruction'
  AND NOT EXISTS (SELECT 1 FROM kefu_artifact_blob b WHERE b.artifact_key = d.artifact_key)
ORDER BY d.created_at
"""

Q2 = """
SELECT d.idempotency_key, d.recipient_staff_id, d.updated_at
FROM kefu_outbound_delivery d
WHERE d.status = 'failed' AND d.payload_type = 'file'
  AND d.artifact_doc_type = 'outbound_instruction'
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        print("No database URL: set DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(args.database_url)
    except psycopg2.Error as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 2
    conn.set_session(readonly=True)

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            print(f"Database: {cur.fetchone()[0]}   (read-only)\n")

            print("Q1  Pending delivery orders queued without stored bytes (deploy gate: expect 0 rows)")
            cur.execute(Q1)
            q1 = cur.fetchall()
            if q1:
                _print_table(["idempotency key", "attempts", "next retry", "last error", "queued"], q1)
                print(f"  -> {len(q1)} pending. Do NOT deploy yet: wait for them to send, then re-run.")
            else:
                print("  -> 0 rows. Safe to deploy.")

            print()
            print("Q2  Delivery orders failed on hash mismatch, last 24h (after deploy, informational)")
            cur.execute(Q2)
            q2 = cur.fetchall()
            if q2:
                _print_table(["idempotency key", "staff", "failed at"], q2)
                print(f"  -> {len(q2)} failed. Send those staff their delivery order again.")
            else:
                print("  -> 0 rows.")
    finally:
        conn.rollback()
        conn.close()

    return 1 if q1 else 0


if __name__ == "__main__":
    sys.exit(main())
