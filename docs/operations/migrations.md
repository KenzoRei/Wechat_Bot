# Database migrations

**Status:** Current
**Owner:** Engineering and operations
**Last verified against commit:** `3def0eb` (2026-09-25)

Migrations are sequential SQL files under `db/migrations/`, currently V1-V35.
The project does not use Alembic or Flyway. `scripts/apply_migrations.py`
applies migrations numerically and records completed versions in
`public.schema_migrations`.

If an existing database has schema objects from migrations that predate the
ledger, do not run the migration runner against an empty ledger: it will try
to replay V1 against existing objects. Verify the actual schema and applied
migration history for that environment before recording any baseline versions.
Recording a version does not execute or validate its SQL.

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

The current runner creates and commits `public.schema_migrations` if it is
missing **before** checking `--dry-run`. The command does not apply migration
SQL, but it is not strictly read-only on a database without that table.

For disposable database provisioning and the V1 `search_path` caveat, see
[Local PostgreSQL test database](../testing/local-postgresql.md).

## Migrate before or after deploying?

Decide per release, by asking whether the **new code** depends on the **new
schema**:

- **New code reads or writes new columns/tables → migrate first, then
  deploy.** Otherwise the new code fails against the old schema the moment it
  starts. Example: V35 had to be applied first, because the new Kefu claim
  query reads `next_attempt_at`; deploying first would have broken every Kefu
  message, not only voice.
- **The migration only adds things the old code ignores** (nullable columns,
  new tables, new catalog rows) → it's safe to apply while the old code runs,
  which makes "migrate first" the default choice.
- **Only the reverse is unsafe:** a migration that removes or renames
  something the running code still uses has to wait until code that no longer
  uses it is deployed.

**Running a migration before its code is deployed:** the Render shell only
contains the *currently deployed* code, so it doesn't have the new migration
file yet. Run it from a local checkout of the commit you're about to deploy,
against the database's **External Database URL** (Render dashboard →
database → Connections; the short internal host only resolves inside
Render):

```powershell
python scripts/apply_migrations.py --database-url "<External Database URL>" --dry-run
python scripts/apply_migrations.py --database-url "<External Database URL>"
```

Check that the dry run lists exactly the expected pending versions. Note that
the script's production-host guard (`_PRODUCTION_HOST_FRAGMENT`) still names
an earlier database host, so it does not currently recognize the production
database. Double-check the URL yourself.
