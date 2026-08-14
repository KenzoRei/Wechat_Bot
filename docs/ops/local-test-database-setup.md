# Local Test Database Setup

**Why this exists:** the integration test suite (`tests/kefu_integration/`, `tests/uchoice_self_registration/` mock tests aside) needs a real PostgreSQL database. Historically that meant running against the shared **production** database, which caused a real incident (see `tests/kefu_integration/test_kefu_admin_purge.py`'s docstring: an unscoped test once cancelled a live customer case). `tests/conftest.py` now refuses to run against the known production host unless `ALLOW_PRODUCTION_DB_TESTS=1` is explicitly set — everything below sets up a genuinely isolated alternative so that flag is never needed for normal test runs.

This was set up 2026-08-14 on a local machine (Dell desktop) after Render's free tier rejected a second free Postgres instance ("cannot have more than one active free tier database") and Docker Desktop failed with "virtualization support not detected" on that hardware. A native local PostgreSQL install was the path of least resistance. If you're setting this up on a different machine, the same steps apply — just re-run them there.

## What's installed (on the machine where this was originally set up)

- PostgreSQL 18, installed via `winget install PostgreSQL.PostgreSQL.18`, running as a Windows service (`postgresql-x64-18`, automatic startup) on port 5432.
- A dedicated database: `wechat_bot_test`, owned by the `postgres` superuser.
- Timezone explicitly set to UTC on that database (`ALTER DATABASE wechat_bot_test SET timezone TO 'UTC'`) to match production — the local install's default was `America/New_York`, which caused one real test failure (a UTC timestamp round-tripped through server-local time landed on the wrong calendar date) before this was set.
- All 16 migrations applied via `scripts/apply_migrations.py`, tracked in a `schema_migrations` ledger table.

**This instance is local to that specific machine** — `127.0.0.1:5432` only, not exposed to the network. It is not reachable from another location (e.g. home) unless you remote into that machine directly. If you want an equivalent database elsewhere, follow the steps below fresh on that machine; there is no way to "connect from home" to a `127.0.0.1`-only service running somewhere else without deliberately setting up network exposure, which is not recommended for a database with superuser access.

## Setting this up fresh on any machine

### 1. Install PostgreSQL

```powershell
winget install PostgreSQL.PostgreSQL.18
```

This installs and starts the service automatically. Check with:

```powershell
Get-Service -Name "*postgresql*"
```

### 2. Find/set the postgres superuser password

If you don't know the password the installer set (or it was never set to anything you'd recognize), reset it:

1. Locate `pg_hba.conf` (find the data directory via `Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\postgresql-x64-18" -Name ImagePath` — look for the `-D` argument).
2. Back up `pg_hba.conf`, then temporarily change the `127.0.0.1/32` and `::1/128` lines' auth method from `scram-sha-256` to `trust`.
3. **In an Administrator PowerShell**: `Stop-Service postgresql-x64-18; Start-Service postgresql-x64-18`.
4. Connect without a password and reset it:
   ```
   ALTER USER postgres PASSWORD '<new password>';
   ```
5. Restore the original `pg_hba.conf` from the backup.
6. **In an Administrator PowerShell** again: `Stop-Service postgresql-x64-18; Start-Service postgresql-x64-18`.
7. Verify: a connection *without* a password should now fail, and one *with* the new password should succeed.

(This is the exact procedure used originally — see the session where this file was created for the full back-and-forth, if you want more context than this summary.)

### 3. Create the test database

```python
import psycopg2
conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="<password>", dbname="postgres")
conn.autocommit = True
conn.cursor().execute("CREATE DATABASE wechat_bot_test")
conn.cursor().execute("ALTER DATABASE wechat_bot_test SET timezone TO 'UTC'")  # match production
```

### 4. Apply migrations

```bash
python scripts/apply_migrations.py --database-url "postgresql://postgres:<password>@127.0.0.1:5432/wechat_bot_test"
```

This applies all 16 files under `db/migrations/` in order and records each in a `schema_migrations` table, so re-running it later only applies anything new.

### 5. Save the connection string locally

Add to your **local** `.env` (gitignored, never committed):

```
TEST_DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/wechat_bot_test
```

### 6. Run tests against it

```bash
DATABASE_URL=$TEST_DATABASE_URL python -m pytest tests/
```

(`conftest.py` reads `DATABASE_URL`, not `TEST_DATABASE_URL` directly — export/alias it for the test run, or set `DATABASE_URL` itself in a test-specific env file.)

You'll also need dummy values for the other required settings if they're not already in your `.env` (`WECHAT_CORP_ID`, `WECHAT_TOKEN`, `WECHAT_ENCODING_AES_KEY`, `YIDIDA_BASE_URL`, `OMS_BASE_URL`, `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `ADMIN_API_KEY`) — any non-empty placeholder works, since `tests/conftest.py`'s `block_operational_clients` fixture prevents any real external call regardless of what these are set to.

## Known gotchas

- **`--import-mode=importlib`**: default pytest collection can fail with an "import file mismatch" error if two test files share a basename in different directories (this repo has `tests/kefu/test_kefu_admin_purge.py` and `tests/kefu_integration/test_kefu_admin_purge.py`). Add `--import-mode=importlib` to the pytest invocation, or clear `__pycache__` if you hit this.
- **`V1`'s `search_path` reset**: `db/migrations/V1__initial_schema.sql` is a `pg_dump`-style baseline that sets `search_path` to empty for the rest of its session, as a security convention. `scripts/apply_migrations.py` already resets it before each migration — worth knowing if you ever run these files a different way.
