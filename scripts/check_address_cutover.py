"""Read-only pre/post-cutover check for the add_address / update_address split (v1.12.0).

Run in the Render shell during the paused window:
    python scripts/check_address_cutover.py

Reports (and changes nothing):
  1. which address services exist in the catalog (upsert_address / add_address / update_address)
  2. unexpired conversation sessions on the address service that still carry
     `matched_address_id` -- old EDIT drafts. add_address is create-only now
     and rejects them with a restart message; they expire on their own, but
     list them so the operator knows who has to start over.
  3. how many unexpired sessions exist overall (should be ~0 once ingress is paused).
"""
import os
import sys

import psycopg2


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True)
    cur = conn.cursor()

    cur.execute("SELECT name, service_type_id FROM service_type WHERE name IN ('upsert_address','add_address','update_address') ORDER BY name")
    services = cur.fetchall()
    print("address services in catalog:", [name for name, _ in services] or "none")
    ids = [str(sid) for name, sid in services if name in ("upsert_address", "add_address")]

    if ids:
        cur.execute(
            """SELECT session_id, status, source_channel, expires_at
                 FROM conversation_session
                WHERE service_type_id::text = ANY(%s)
                  AND expires_at > now()
                  AND collected_fields ? 'matched_address_id'
                ORDER BY expires_at""",
            (ids,),
        )
        edit_drafts = cur.fetchall()
        print(f"open EDIT drafts on the address service: {len(edit_drafts)}")
        for row in edit_drafts:
            print("  ", row)
    cur.execute("SELECT count(*) FROM conversation_session WHERE expires_at > now()")
    print("unexpired sessions overall:", cur.fetchone()[0])
    conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
