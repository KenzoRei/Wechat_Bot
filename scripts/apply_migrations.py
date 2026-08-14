"""
Migration runner + ledger -- closes the gap flagged repeatedly across the
cross-review (docs/ai-collaboration/audit_20260814/): this project has 16
hand-rolled SQL files under db/migrations/ but no tool that applies them in
order or records which ones a given database has already seen.

Usage:
    python scripts/apply_migrations.py --database-url postgresql://...
    python scripts/apply_migrations.py --database-url postgresql://... --dry-run

Or set DATABASE_URL / TEST_DATABASE_URL in the environment instead of
passing --database-url.

Safety: refuses to run against the known production database host unless
--allow-production is explicitly passed, mirroring the same
default-deny-production discipline as tests/conftest.py's
ALLOW_PRODUCTION_DB_TESTS gate. This tool is for standing up a fresh
(test) database from scratch, or re-syncing a fresh clone of the schema --
running it against production is very unlikely to be what you want, since
production is already at V16 and rerunning idempotent-unsafe DDL (e.g.
V2's INSERTs) against a populated database would fail or duplicate data.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
_PRODUCTION_HOST_FRAGMENT = "dpg-d9p26h7lk1mc73a841d0-a.virginia-postgres.render.com"

_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version     INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _discover_migrations() -> list[tuple[int, Path]]:
    pattern = re.compile(r"^V(\d+)__.*\.sql$")
    found = []
    for path in MIGRATIONS_DIR.glob("V*.sql"):
        m = pattern.match(path.name)
        if not m:
            continue
        found.append((int(m.group(1)), path))
    found.sort(key=lambda pair: pair[0])
    return found


def _strip_psql_meta_commands(sql_text: str) -> str:
    """psycopg2 executes raw SQL, not psql's own client-side meta-commands
    (e.g. this project's migrations start with `\\encoding UTF8`)."""
    return "\n".join(
        line for line in sql_text.splitlines() if not line.lstrip().startswith("\\")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true", help="show what would run, apply nothing")
    parser.add_argument("--allow-production", action="store_true",
                         help="required to run against the known production database host")
    args = parser.parse_args()

    database_url = (
        args.database_url
        or os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        print("No database URL: pass --database-url or set TEST_DATABASE_URL/DATABASE_URL.", file=sys.stderr)
        return 1

    if _PRODUCTION_HOST_FRAGMENT in database_url and not args.allow_production:
        print(
            "Refusing to run: this URL points at the known production database host, "
            "and --allow-production was not passed. This tool is meant for a fresh "
            "test database, not for re-running migrations against production.",
            file=sys.stderr,
        )
        return 1

    migrations = _discover_migrations()
    if not migrations:
        print(f"No migration files found under {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_LEDGER)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT version FROM public.schema_migrations")
            already_applied = {row[0] for row in cur.fetchall()}

        pending = [(v, p) for v, p in migrations if v not in already_applied]
        if not pending:
            print(f"Up to date: all {len(migrations)} migrations already applied.")
            return 0

        print(f"{len(already_applied)} already applied, {len(pending)} pending:")
        for version, path in pending:
            print(f"  V{version}: {path.name}")

        if args.dry_run:
            print("\n--dry-run: applied nothing.")
            return 0

        for version, path in pending:
            sql_text = _strip_psql_meta_commands(path.read_text(encoding="utf-8"))
            print(f"\nApplying V{version}: {path.name} ...")
            try:
                with conn.cursor() as cur:
                    # V1 (a pg_dump-style baseline) includes
                    # `SELECT pg_catalog.set_config('search_path', '', false)`
                    # -- a session-scoped (not transaction-local) empty
                    # search_path, a common pg_dump security convention that
                    # forces the dump's own statements to be fully-qualified.
                    # It has no matching restore, so it silently persists
                    # for the rest of this connection unless reset here --
                    # otherwise every following migration's (and this
                    # script's own) unqualified table references break.
                    cur.execute("SET search_path TO public")
                    cur.execute(sql_text)
                    cur.execute(
                        "INSERT INTO public.schema_migrations (version, filename) VALUES (%s, %s)",
                        (version, path.name),
                    )
                conn.commit()
                print(f"  OK, recorded as applied.")
            except Exception as exc:
                conn.rollback()
                print(f"  FAILED: {exc}", file=sys.stderr)
                print(f"\nStopped at V{version}. Migrations before this one were committed "
                      f"and recorded; this one and everything after it were not applied.",
                      file=sys.stderr)
                return 1

        print(f"\nDone: applied {len(pending)} migration(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
