# Render deployment and cutover

**Status:** Current
**Owner:** Operations
**Last reviewed:** 2026-08-14

Production runs on Render. Hosting rationale is recorded in
[ADR-008](../architecture/decisions/adr-008-render-hosting.md).

## Deployment checklist

1. Back up PostgreSQL before migration or service cutover.
2. Apply pending SQL migrations in order and record the result operationally.
3. Configure channel flags explicitly; do not rely on defaults in production.
4. Confirm every enabled mode has only its required credentials.
5. Set `SERVER_BASE_URL` to the deployed HTTPS origin.
6. Ensure exactly one process has `RUN_SCHEDULER=true`.
7. Set a stable `WORKER_INSTANCE_ID` for the scheduler/worker owner.
8. Deploy and verify `/health/live` followed by `/health/ready`.
9. Verify disabled routes return 404.
10. Send a real signed message through each enabled WeCom channel.
11. Inspect sessions, request logs, Kefu queues, and scheduled-job logs.

## Secret rotation

Changing an environment variable triggers a Render deploy/restart. Rotating
`ADMIN_API_KEY` does not alter bot processing credentials, but existing admin
panel sessions and API callers must enter the new key. Rotate provider secrets
independently and verify the affected channel/integration after restart.

## Rollback

- Revert application/configuration changes through a known-good deployment.
- Treat applied SQL migrations as forward-only; correct them with a new
  migration rather than editing history.
- Keep both pipeline implementations during the initial observation window.
- If a callback URL changes, re-register and verify it in the WeCom console.
