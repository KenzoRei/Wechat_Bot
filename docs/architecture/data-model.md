# Data model

**Status:** Current overview
**Owner:** Engineering
**Last verified against commit:** `aaf3191` (2026-09-11)

PostgreSQL schema is defined by the ordered SQL files in `db/migrations/` and
represented at runtime by models in `models/`. If this overview conflicts with
a constraint or column in those sources, the migration/model is authoritative.

## Main domains

| Domain | Principal tables |
|---|---|
| Groups and authorization | `group_config`, `group_member`, `role`, `group_service`, `role_service_permission` |
| Service catalog | `service_type`, `workflow`, `workflow_step` |
| Conversation lifecycle | `conversation_session`, `request_log`, `interaction_log` |
| U-Choice | `uchoice_customer`, `uchoice_sku`, `uchoice_storage`, `uchoice_storage_txn`, `uchoice_address`, fee/digest tables |
| Kefu identities and durability | `kefu_staff`, `case_turn`, `case_execution`, staff-case context, inbound/sync/delivery tables |
| Customer master data and labels | `customer`, `customer_credential`, `label_shipment` |
| Company warehouse directory | `company_warehouse` |

## Cross-channel identity

- Smart Bot members are represented by `group_member.wechat_openid` plus
  `group_id`.
- Kefu staff are represented by `kefu_staff.staff_id` and provider identities.
- Sessions and logs carry `source_channel` and channel-specific actor fields.
- Active-admin invariants count active administrators across both member tables.

## Lifecycle rules

- In-progress sessions use `active` or `pending_confirmation`.
- Request logs retain the durable business outcome.
- Kefu turn and execution ledgers provide replay/idempotency boundaries distinct
  from Smart Bot processing.
- U-Choice inventory mutation is recorded in transaction history and protected
  by PostgreSQL locking/constraints.

## Authorization model

A role's actually-reachable services in a group are the intersection of two,
independently-managed tables:

- `role_service_permission` — **global**, role → service. Deny-by-default:
  a role has zero access to any service until a row here grants it,
  regardless of group. Managed via `GET/POST/DELETE
  /admin/roles/{role_id}/services[/{service_type_id}]`.
- `group_service` — per-group, which services a tenant/group has enabled at
  all (and its per-group config, e.g. YiDiDa/OMS credentials). Still the
  real multi-tenancy boundary.

The older `group_service_role` (per-group role→service grants) was replaced
by `role_service_permission` in `V30` — see
[`models/role.py`](../../models/role.py)'s `RoleServicePermission` docstring
for the rationale (Kefu has exactly one group system-wide, and Smart Bot's
available services are meant to be consistent across groups, so per-group
role scoping added no real isolation).

`ASSIGNABLE_ROLE_NAMES` (`core/role_registry.py`) is a separate, hardcoded
allowlist gating which role *names* can be assigned to a member/staff at
all — a `role` table row alone doesn't make a role assignable.

## Migration authority

Migrations are sequential SQL files, currently V1 through V30. They are
forward-only operational SQL; the project does not currently use Alembic,
Flyway, or a schema-version ledger. See [Migrations](../operations/migrations.md).

The previous detailed V1/V3–V8 data dictionary is preserved as
[historical design](../archive/designs/data-model-v2.1.md).
