# Local PostgreSQL test database

**Status:** Current
**Owner:** Engineering
**Last verified against commit:** `7bf8395` plus the pending test-suite cleanup (2026-08-14)

PostgreSQL integration tests exercise locking, concurrency, constraints, and
transaction behavior that SQLite cannot reproduce. They must run against a
disposable database, never a shared operational or production database.

The original development machine uses PostgreSQL 18 on Windows, listening only
on `127.0.0.1:5432`, with a database named `wechat_bot_test`. Its database
timezone is UTC to match production and prevent date assertions from changing
with the host timezone.

## Create a local database

Install PostgreSQL on Windows if it is not already available:

```powershell
winget install PostgreSQL.PostgreSQL.18
Get-Service -Name "*postgresql*"
```

Create a dedicated login and database using `psql` or an administration tool.
Do not use the PostgreSQL superuser as the routine test credential. The login
should have access only to this disposable database.

```sql
CREATE ROLE wechat_bot_test LOGIN PASSWORD '<password>';
CREATE DATABASE wechat_bot_test OWNER wechat_bot_test;
ALTER DATABASE wechat_bot_test SET timezone TO 'UTC';
```

Keep the service bound to localhost. Do not expose a developer database to the
internet merely to share it between machines; create an equivalent disposable
database on each machine or use an ephemeral CI service.

## Apply the schema

Set the test URL only for the current shell, then apply all pending migrations:

```powershell
$env:TEST_DATABASE_URL = "postgresql://wechat_bot_test:<password>@127.0.0.1:5432/wechat_bot_test"
python scripts/apply_migrations.py
```

The migration runner applies `db/migrations/V1...V16` numerically and records
completed versions in `public.schema_migrations`. It resets `search_path`
before each migration because the V1 pg_dump-style baseline clears it for the
connection. Re-running the command applies only migrations absent from the
ledger.

Use `--dry-run` to inspect pending migrations. Although the runner currently
has an explicit production override for operational use, never use that option
while preparing or running tests.

## Run the suites

```powershell
python -m pip install -r requirements-dev.txt
pytest -m "not postgres and not live"

$env:TEST_DATABASE_URL = "postgresql://wechat_bot_test:<password>@127.0.0.1:5432/wechat_bot_test"
pytest -m postgres
```

Plain `pytest` remains safe: without an explicitly supplied
`TEST_DATABASE_URL`, PostgreSQL tests are collected but skipped. Test
configuration supplies inert values for unrelated application settings and
blocks outbound operational clients.

Do not commit the URL or password. A local `.env` may hold developer secrets,
but the PostgreSQL test opt-in intentionally requires the variable in the
pytest process environment rather than silently enabling integration tests
from `.env`.

## Troubleshooting

- Import-file mismatch errors should not occur because `pytest.ini` uses
  `--import-mode=importlib` and integration modules have distinct names.
- If date assertions differ by one day, verify `SHOW timezone;` returns `UTC`.
- If a migration fails after V1, use the migration runner rather than executing
  every SQL file in one session without restoring `search_path`.
- If pytest refuses the URL, confirm it is a disposable local/test database;
  the known production host is rejected with no test-suite bypass.
