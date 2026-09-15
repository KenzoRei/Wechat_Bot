# Product overview

**Status:** Current
**Owner:** Product and operations
**Last verified against commit:** `f3492b6` (2026-09-15)

The platform turns informal WeCom logistics conversations into validated,
auditable service requests. It supports carrier-label workflows and U-Choice
warehouse operations through two WeCom channel types.

## Supported channels

- **WeCom Smart Bot:** internal group-chat interaction through `/webhook`.
- **WeCom Kefu:** staff/customer-service interaction through
  `/kefu/callback`, durable inbound processing, and a durable outbound queue.

Both channels share service permissions, business validation, workflow
handlers, inventory, request logs, and administrative roles. They do not promise
identical transport behavior or response rendering.

## Current operational capabilities

- AI-assisted service classification and structured field extraction.
- Deterministic server-side validation before persistence and execution.
- Confirmation and cancellation lifecycle with durable request logs.
- FedEx/UPS label and OMS integrations, backed by a central `F######`-keyed
  customer master-data service (`core/customer_directory.py`) holding
  per-carrier YDD/OMS credentials and the `billing_customer_id` binding a
  customer-identity role's holder is bound to.
- A company shipping-origin directory (`core/warehouse_directory.py`) that
  resolves a bare warehouse abbreviation (e.g. "从LAX到DE") to full
  shipper/recipient address info in label requests.
- U-Choice inbound, outbound, storage, address, role, and invoice workflows.
- Role-based service grants — global (`role_service_permission`, not
  per-group), currently spanning `admin`, `customer`, `warehouseman`,
  `warehouse_admin`, `accountant`, `label_agent`, and `fedex_label_agent`.
- Kefu staff self-registration into a non-privileged pending state.
- Admin API and browser panel for configuration and role/staff assignment.

## Current constraints

- Production is a low-volume, single-company deployment.
- Exactly one process may own APScheduler jobs.
- Smart Bot processing is not yet a durable post-ack queue.
- Kefu has stronger durable mechanics but remains subject to WeCom account and
  reply-window constraints.
- SQL migrations are applied via `scripts/apply_migrations.py`, which records
  applied versions in `public.schema_migrations` (see
  [Migrations](../operations/migrations.md)).
- Integration tests requiring PostgreSQL need an isolated test database before
  they are safe as a routine suite.

The original v1 requirements and kickoff material are preserved under
[archive/project-origin](../archive/project-origin/).
