# ADR-008: Use Render for production hosting

**Date:** 2026-08-14
**Status:** Decided
**Supersedes:** ADR-005 production-hosting choice

## Decision

Use Render for the production FastAPI service and PostgreSQL database. Keep the
deployment low-complexity and single-instance while traffic is low.

## Rationale

- The application is already deployed and operational on Render.
- Managed HTTPS, environment variables, process restarts, and PostgreSQL avoid
  unnecessary server administration.
- AI inference and logistics-provider calls are external, so the application
  does not require local inference compute.
- Staying on the current host avoids callback URL and database-migration risk.

## Constraints

- Exactly one process may run APScheduler until leader election or a dedicated
  worker service exists.
- Database backup/restore and migration application remain explicit operational
  procedures.
- Pricing and plan availability must be rechecked when purchasing or changing
  service tiers.

## Consequences

- Render configuration and environment variables are production-critical state.
- Health checks use `/health/live` and `/health/ready`.
- Horizontal scaling requires a scheduler/worker topology decision first.
