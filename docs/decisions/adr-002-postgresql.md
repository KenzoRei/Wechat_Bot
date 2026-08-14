# ADR-002: Use PostgreSQL as the database

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use PostgreSQL as the primary database.

## Alternatives considered
- **MySQL** — simpler relational database; lacks native JSONB column support which we use heavily for WorkflowStep.config, parsed_input, and input_schema
- **MongoDB** — document database; designed for unstructured data; our data is deeply relational (users → groups → services → workflows → steps → logs) making a document database awkward and query-intensive
- **Redis** — in-memory only; appropriate for caching and queues (noted as v2 option) but not for permanent storage

## Reason
- Our schema is deeply relational — foreign keys, joins, and referential integrity are core requirements
- JSONB column support allows storing flexible structured data (configs, parsed inputs, schemas) alongside relational data in the same database
- Strong constraint enforcement at the database level prevents data integrity issues
- Developer already has PostgreSQL experience from a previous project — no new learning required
- Industry standard with excellent tooling and community support

## Consequences
- Requires a running PostgreSQL instance in all environments (local, production)
- Schema changes require migration files. In practice the project uses
  hand-rolled, sequentially numbered SQL files under `db/migrations/`
  (`V1__...sql` through `V16__...sql` as of 2026-08), not Alembic —
  corrected 2026-08-14; this ADR and ADR-003 originally said Alembic, which
  was never actually adopted. There is currently no migration-runner tooling
  or applied-migration ledger in the repository; see the signed cross-review
  plan (`docs/audit_20260814/`) Section C6 for the follow-up to add one.
