# Database migrations

**Status:** Current
**Owner:** Engineering and operations
**Last verified against commit:** `7bf8395` plus the pending migration runner (2026-08-14)

Migrations are sequential SQL files under `db/migrations/`, currently V1-V16.
The project does not use Alembic or Flyway. `scripts/apply_migrations.py`
applies migrations numerically and records completed versions in
`public.schema_migrations`.

## Rules

1. Never edit a migration already applied to a persistent environment.
2. Add the next numbered migration for every schema or catalog correction.
3. Back up the target database before applying production migrations.
4. Use `scripts/apply_migrations.py`; do not implement filename ordering in an
   ad hoc command.
5. Preserve the database ledger and a release record of who ran the migration
   and against which environment.
6. Verify schema constraints and seeded workflow rows after application.
7. Test V1 through latest against an empty isolated PostgreSQL database before
   relying on bootstrap reproducibility.

Inspect pending migrations without applying them:

```powershell
python scripts/apply_migrations.py --database-url "postgresql://..." --dry-run
```

For disposable database provisioning and the V1 `search_path` caveat, see
[Local PostgreSQL test database](../testing/local-postgresql.md).
