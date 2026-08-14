# Test strategy and safety

**Status:** Current
**Owner:** Engineering
**Last verified against commit:** `7bf8395` plus the pending test-suite cleanup (2026-08-14)

## Test layers

- Offline unit/contract tests must use fakes or isolated SQLite where supported.
- PostgreSQL-specific locking, constraints, and concurrency tests require a
  dedicated PostgreSQL test database.
- Live provider tests are manual, default-off, and require explicit opt-in.

## Safety rules

1. Never run mutation tests against production or a shared operational database.
2. A PostgreSQL test URL must carry an unmistakable test-only database/schema
   marker and use credentials restricted to that target.
3. Test-created rows must use unique identifiers and deterministic cleanup.
4. Tests must not select an arbitrary existing group/customer as their fixture.
5. External clients are blocked by default in the automated suite.
6. Live smoke scripts belong under `scripts/`, not pytest discovery, and require
   an explicit opt-in environment flag.
7. CI runs secret scanning across history and the normal offline suite.

Pytest classifies real-PostgreSQL cases with the `postgres` marker. Without a
`TEST_DATABASE_URL` supplied explicitly to the pytest process, those cases are
collected but skipped. The known production host is rejected without a test
override.

Commands and local database provisioning are documented in
[Local PostgreSQL test database](local-postgresql.md).
