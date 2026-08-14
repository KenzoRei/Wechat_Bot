# Data model

**Status:** Current overview
**Owner:** Engineering
**Last verified against commit:** `c89cf6f` (2026-08-14)

PostgreSQL schema is defined by the ordered SQL files in `db/migrations/` and
represented at runtime by models in `models/`. If this overview conflicts with
a constraint or column in those sources, the migration/model is authoritative.

## Main domains

| Domain | Principal tables |
|---|---|
| Groups and authorization | `group_config`, `group_member`, `role`, `group_service`, `group_service_role` |
| Service catalog | `service_type`, `workflow`, `workflow_step` |
| Conversation lifecycle | `conversation_session`, `request_log`, `interaction_log` |
| U-Choice | `uchoice_customer`, `uchoice_sku`, `uchoice_storage`, `uchoice_storage_txn`, `uchoice_address`, fee/digest tables |
| Kefu identities and durability | `kefu_staff`, `case_turn`, `case_execution`, staff-case context, inbound/sync/delivery tables |

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

## Migration authority

There are currently sixteen sequential migrations, V1 through V16. They are
forward-only operational SQL; the project does not currently use Alembic,
Flyway, or a schema-version ledger. See [Migrations](../operations/migrations.md).

The previous detailed V1/V3–V8 data dictionary is preserved as
[historical design](../archive/designs/data-model-v2.1.md).
