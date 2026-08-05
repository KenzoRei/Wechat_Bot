# Data Model
# Logistics WeChat Bot Platform — v1

**Version:** 2.1 — U-Choice pipeline added (V3–V8)
**Date:** 2026-08-05
**Status:** Finalized (base platform); U-Choice implemented and live-tested, see `docs/uchoice-design.md` for the original design reasoning and `docs/ops/adding-a-service.md` for the process used to build it out

---

## Entity Relationship Diagram

```mermaid
erDiagram
    group_config {
        uuid group_id PK
        varchar wechat_group_id UK
        varchar description
        boolean is_active
        integer daily_request_limit
        timestamptz created_at
        timestamptz updated_at
    }

    group_member {
        varchar wechat_openid PK
        uuid group_id PK_FK
        uuid role_id FK
        varchar display_name
        boolean is_active
        timestamptz joined_at
        timestamptz updated_at
    }

    role {
        uuid role_id PK
        varchar name UK
        varchar description
        timestamptz created_at
    }

    group_service {
        uuid group_id PK_FK
        uuid service_type_id PK_FK
        uuid workflow_id FK
        jsonb config
    }

    group_service_role {
        uuid group_id PK_FK
        uuid service_type_id PK_FK
        uuid role_id PK_FK
        varchar created_by
        timestamptz created_at
    }

    service_type {
        uuid service_type_id PK
        varchar name UK
        varchar description
        jsonb input_schema
        jsonb group_config_schema
        text confirmation_note
        boolean is_active
        timestamptz created_at
    }

    workflow {
        uuid workflow_id PK
        varchar name UK
        text description
        timestamptz created_at
    }

    workflow_step {
        uuid step_id PK
        uuid workflow_id FK
        smallint step_order
        varchar step_type
        jsonb config
    }

    conversation_session {
        uuid session_id PK
        varchar wechat_openid
        uuid group_id FK
        uuid service_type_id FK
        varchar status
        jsonb conversation_history
        jsonb collected_fields
        uuid request_log_id FK
        timestamptz expires_at
        timestamptz created_at
        timestamptz updated_at
    }

    request_log {
        uuid log_id PK
        varchar serial_number UK
        varchar wechat_openid
        uuid group_id FK
        uuid service_type_id FK
        varchar status
        text raw_message
        jsonb parsed_input
        jsonb result
        text error_detail
        varchar wechat_msg_id UK
        timestamptz created_at
        timestamptz completed_at
    }

    group_config ||--o{ group_member      : "has members"
    group_config ||--o{ group_service     : "has services"
    group_config ||--o{ conversation_session : "has sessions"
    group_config ||--o{ request_log       : "has requests"

    service_type ||--o{ group_service     : "used by groups"
    service_type ||--o{ conversation_session : "classified as"
    service_type ||--o{ request_log       : "recorded as"

    workflow     ||--o{ group_service     : "run by"
    workflow     ||--o{ workflow_step     : "contains"

    role         ||--o{ group_member      : "held by members"
    role         ||--o{ group_service_role : "granted to"
    group_service ||--o{ group_service_role : "gated by"

    conversation_session ||--o| request_log : "becomes"
```

### U-Choice tables (added V3–V8)

Deliberately a separate diagram — these tables have **no `group_id`**, on
purpose. U-Choice owns its own packing-supply inventory; it's not a
multi-tenant 3PL storing separate customers' goods, so there's no "whose
pallets" question for these tables to answer. See `docs/uchoice-design.md`
for the full reasoning.

```mermaid
erDiagram
    uchoice_sku {
        varchar sku_code PK
        varchar description
    }

    uchoice_storage {
        varchar warehouse_code PK
        varchar sku_code PK_FK
        integer boxes_per_pallet PK
        integer pallet_count
        timestamptz updated_at
    }

    uchoice_storage_txn {
        uuid txn_id PK
        varchar warehouse_code
        varchar sku_code
        integer boxes_per_pallet
        integer pallet_delta
        varchar txn_type
        uuid request_log_id FK
        text note
        varchar created_by
        timestamptz created_at
    }

    uchoice_address {
        uuid address_id PK
        varchar company_name
        varchar charge_type
        text addr
        varchar warehouse_code
        text note
        varchar created_by
        timestamptz created_at
    }

    uchoice_storage_fee_ledger {
        uuid ledger_id PK
        varchar warehouse_code
        date fee_date
        integer pallet_count
        numeric storage_fee
    }

    interaction_log {
        uuid interaction_id PK
        varchar wechat_openid
        uuid group_id FK
        varchar intent
        varchar intent_type
        uuid service_type_id FK
        uuid request_log_id FK
        timestamptz created_at
    }

    uchoice_sku ||--o{ uchoice_storage : "tracked as"
    uchoice_storage_txn }o--o| request_log : "caused by"
```

---

## Table Summary

| Table | Purpose | Type |
|---|---|---|
| `group_config` | Each WeChat Work group | Config |
| `group_member` | Who is in each group + role | Config |
| `role` | Role catalog (admin, customer, ...) — extensible via admin API | Config |
| `group_service` | Which services + which workflow per group | Config |
| `group_service_role` | Which roles can use which service, per group — deny-by-default | Config |
| `service_type` | Service definitions + field schemas for Claude | Config |
| `workflow` | Named workflow definitions | Config |
| `workflow_step` | Ordered steps per workflow | Config |
| `conversation_session` | Active multi-turn conversations | Runtime (temporary) |
| `request_log` | Permanent request history | Runtime (permanent) |
| `interaction_log` | Write-once log of every classified message, regardless of outcome — funnel/efficiency analysis | Runtime (permanent, append-only) |
| `uchoice_sku` | U-Choice's 8-product catalog (stretch wrap, tape) | Config |
| `uchoice_storage` | Current balance per (warehouse, sku, boxes_per_pallet) bucket — **no `group_id`**, company-wide inventory | Runtime |
| `uchoice_storage_txn` | Audit log of every storage mutation | Runtime (permanent) |
| `uchoice_address` | Shared address book for outbound destinations and inter-warehouse transfers — **no `group_id`** | Config/Runtime |
| `uchoice_storage_fee_ledger` | Daily storage-fee snapshot per warehouse, populated by `jobs/uchoice_daily.py` | Runtime (permanent) |

---

## Key Design Decisions

| Decision | Detail |
|---|---|
| `workflow_id` lives in `group_service` | Same service type can run different workflows per group — e.g. Group A: FedEx + OMS, Group B: FedEx only |
| `daily_request_limit` in `group_config` | Per-group daily request cap. NULL = unlimited. **Column exists but is not yet enforced anywhere in code** — reserved for future use, added now to avoid a migration later. |
| No separate `user` table | `wechat_openid` used directly as user identifier — stable, permanent, no sync issues |
| `display_name` in `group_member` | User names stored per-group membership, not globally |
| Bot ignores non-members silently | Only users in `group_member` get any response |
| One active session per user per group | Enforced by partial unique index on `conversation_session` |
| Session expiry: 1 hour | `expires_at = now() + INTERVAL '1 hour'`; background job notifies user and admin on expiry |
| Serial number: `REQ-YYYYMMDD-000001` | Global sequence, 6-digit padding, never resets |
| `request_log` only for submitted requests | Unclassified/rejected messages close the session only — no `request_log` entry |
| `confirmation_note` in `service_type` | Optional per-service disclaimer shown at the bottom of the confirmation template. NULL = no note. Stored as a plain TEXT column — not in `input_schema` — for clean separation of concerns. |
| `group_config_schema` in `service_type` | Defines the required/optional config keys the admin must supply per group (e.g. `ydd_cust_id`, `ydd_channel_id`). Same structure as `input_schema`. Validated by the API on `POST /admin/groups/{id}/services`. |
| `config` in `group_service` | Holds group-specific API credentials and params (e.g. YiDiDa customer ID). Validated against `group_config_schema` on write. Merged with `workflow_step.config` at runtime before being passed to the handler. |
| Circular FK resolved with `ALTER TABLE` | `conversation_session.request_log_id` added after `request_log` to avoid circular dependency |

---

## Data Dictionary

Per-column detail for every table. Types shown are SQLAlchemy mapped types (`models/*.py`); actual Postgres types follow directly (`UUID`, `JSONB`, etc).

### `group_config`
One row per WeChat group chat. The root of all group-scoped data.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `group_id` | UUID | PK | `gen_random_uuid()` | Internal identifier, used by every other table's `group_id` FK |
| `wechat_group_id` | varchar(128) | NOT NULL, UNIQUE | — | WeChat's own group chat ID — the value webhook messages arrive tagged with; this is the join key between "a message came in" and "which group config applies" |
| `description` | varchar(500) | nullable | — | Human-readable group name (e.g. "Test Group"); also used to build `keHuDanHao` (客户单号) in label handlers |
| `is_active` | boolean | NOT NULL | `true` | If false, `access_control.check_access()` silently ignores all messages from this group — no reply sent |
| `daily_request_limit` | integer | nullable | — | Per-group daily cap. NULL = unlimited. **Column exists but is not yet enforced anywhere in code** |
| `context` | JSONB | nullable | — | Free-form group knowledge injected into the AI prompt — currently used for `location_presets` (named shipper/recipient address shortcuts like "LAX", "DE") |
| `group_robot_webhook_url` | text | nullable | — | *(V3)* WeChat Work Group Robot Webhook URL — a persistent, static URL (unlike `response_url`, single-use per inbound message). Used for scheduled/proactive pushes: daily broadcast, monthly invoice, cross-group completion notifications, and file attachments (`response_url` cannot send files at all — confirmed against the official docs; file delivery is only possible via this webhook, meaning it always goes to the whole group, never privately to one requester). NULL = these pushes silently no-op for that group. |
| `created_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Standard audit timestamps |

### `group_member`
Who can talk to the bot in a given group, and what role they hold there. Composite PK — see note below.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `wechat_openid` | varchar(128) | PK (1/2) | — | WeChat user ID — the `from_user` field on every incoming message |
| `group_id` | UUID | PK (2/2), FK → `group_config.group_id` ON DELETE CASCADE | — | Which group this membership row applies to |
| `role_id` | UUID | NOT NULL, FK → `role.role_id` ON DELETE RESTRICT | — | Which role this member holds in this group. Resolved to a role *name* string (e.g. `"admin"`) before being loaded into the AI prompt context and into `AccessResult.role` |
| `display_name` | varchar(200) | nullable | — | Name shown in bot replies, confirmation templates, and request logs. Scoped per-group deliberately — same person can have a different display name in different groups |
| `warehouse_code` | varchar(20) | nullable | — | *(V3)* Which U-Choice warehouse (`JFK`/`DE`) this member is responsible for. Required-for-`warehouseman`/cleared-on-role-change-away is enforced at the API layer (`api/admin/members.py`), not a DB CHECK — a CHECK can't reach across the FK to know the role's name |
| `is_active` | boolean | NOT NULL | `true` | Suspended members get a permission-denied reply instead of being silently ignored |
| `joined_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Audit timestamps |

**Note on the composite key:** PK is `(wechat_openid, group_id)` only — `role_id` is not part of the key. This means one user has exactly **one** row, and therefore **one** role, per group at any given time. The same user can hold different roles across different groups (separate rows, separate `group_id`), but cannot hold two roles simultaneously within one group.

`ON DELETE RESTRICT` on `role_id` is deliberate: a role can't be deleted from the `role` table while any `group_member` still holds it — forces an explicit reassignment first, rather than silently orphaning members.

### `role`
Catalog of role names. Roles are added via `POST /admin/roles` — no redeploy required, unlike the hardcoded Python set this replaced during design.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `role_id` | UUID | PK | `gen_random_uuid()` | Referenced by `group_member.role_id` and `group_service_role.role_id` |
| `name` | varchar(20) | NOT NULL, UNIQUE | — | The role identifier — `"admin"`, `"customer"`, etc. This is the string surfaced at the API boundary (request/response bodies use the name, not the UUID) |
| `description` | varchar(200) | nullable | — | Human-readable explanation, shown in `GET /admin/roles` |
| `created_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

### `group_service`
Which services a group can use, which workflow runs for each, and the credentials needed to run it.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `group_id` | UUID | PK (1/2), FK → `group_config.group_id` ON DELETE CASCADE | — | The group this assignment belongs to |
| `service_type_id` | UUID | PK (2/2), FK → `service_type.service_type_id` ON DELETE CASCADE | — | Which service is being assigned |
| `workflow_id` | UUID | NOT NULL, FK → `workflow.workflow_id` ON DELETE RESTRICT | — | Which workflow runs when this service is confirmed. Deliberately lives here (not on `service_type`) so the *same* service type can run different workflows for different groups — e.g. Group A's `fedex_label` includes an OMS step, Group B's doesn't |
| `config` | JSONB | NOT NULL | `'{}'` | **Per-group API credentials, in plaintext** — `ydd_api_key`, `oms_app_secret`, warehouse codes, etc. Validated against `service_type.group_config_schema.required` on write (`POST /admin/groups/{id}/services`). Merged with `workflow_step.config` at runtime and passed into each handler. See Security Notes below — this column is not encrypted |

### `group_service_role`
**Deny-by-default permission grant.** A `(group_id, service_type_id)` pair assigned via `group_service` is invisible to a role unless a matching row exists here — no exceptions, no implicit admin bypass at the query level. When onboarding a new group, remember to grant `admin` (and any other needed role) access to each service right after assigning it via `group_service`, or that service will be invisible to everyone.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `group_id` | UUID | PK (1/3) | — | Part of composite FK → `group_service (group_id, service_type_id)` |
| `service_type_id` | UUID | PK (2/3) | — | Part of composite FK → `group_service (group_id, service_type_id)` |
| `role_id` | UUID | PK (3/3), FK → `role.role_id` ON DELETE CASCADE | — | The role being granted access |
| `created_by` | varchar(128) | NOT NULL | — | Who granted this. **Currently manually supplied** in the request body — `ADMIN_API_KEY` is a single shared token with no per-admin identity yet, so this can't be derived from auth automatically |
| `created_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

**Composite FK:** `(group_id, service_type_id)` → `group_service (group_id, service_type_id)` ON DELETE CASCADE — you cannot grant a role access to a service the group was never assigned in the first place; and removing a `group_service` assignment automatically cleans up any grants that referenced it.

**Query pattern** (used in `access_control.check_access()` when building `allowed_services`): for each `group_service` row, join against `group_service_role` on `(group_id, service_type_id, member's role_id)`. Zero matching rows → excluded from `allowed_services`, full stop. This runs once per incoming message.

**Admin API:**
- `POST /admin/groups/{group_id}/services/{service_type_id}/roles` — grant (`{"role": "warehouseman", "created_by": "kenzo"}`)
- `GET /admin/groups/{group_id}/services/{service_type_id}/roles` — list current grants
- `DELETE /admin/groups/{group_id}/services/{service_type_id}/roles/{role_name}` — revoke

### `service_type`
Defines one kind of request the bot can handle — what fields the AI must collect, and what config a group needs to enable it.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `service_type_id` | UUID | PK | `gen_random_uuid()` | Referenced by `group_service`, `conversation_session`, `request_log` |
| `name` | varchar(100) | NOT NULL, UNIQUE | — | Machine-readable identifier the AI returns in `service_type_name` (e.g. `"fedex_label"`) — this is the string that connects an AI response back to a DB row |
| `description` | varchar(500) | nullable | — | Human-readable summary, shown in `/admin/service-types` |
| `input_schema` | JSONB | NOT NULL | `'{}'` | Tells the AI what to collect from the *customer*: `{required: [...], optional: [...], field_hints: {...}}`. Sent to the AI verbatim (minus credentials) inside the system prompt |
| `group_config_schema` | JSONB | NOT NULL | `'{}'` | Tells the admin API what to require in `group_service.config` when assigning this service to a group. Same `{required, optional, field_hints}` shape as `input_schema`, different purpose |
| `confirmation_note` | text | nullable | — | Optional disclaimer appended to the confirmation message shown to the customer before they confirm (e.g. billing terms). **Must be Chinese** — it renders verbatim in an otherwise-Chinese message; every note in the original U-Choice seed data was written in English and had to be translated in a follow-up migration once this was noticed live |
| `is_active` | boolean | NOT NULL | `true` | Inactive service types are excluded from `/admin/service-types` and from `allowed_services` in access control |
| `requires_confirmation` | boolean | NOT NULL | `true` | *(V3)* `false` skips the confirm/cancel template entirely — the workflow executes immediately once `all_fields_collected` fires. Used by pure read-only queries (`view_storage`, `view_storage_history`, `view_invoice`) |
| `targets_existing_request` | boolean | NOT NULL | `false` | *(V3)* `true` means this service locates and updates an existing `request_log` row (by `reference_serial`) instead of creating a new one — the `confirm_inbound_completion`/`confirm_outbound_completion` pattern. See `docs/ops/adding-a-service.md` §7 for the direction-check gap this pattern needs to guard against |
| `awaits_completion` | boolean | NOT NULL | `false` | *(V4)* `true` means confirming this service does NOT mean the job is done — the log stays at `status='processing'` until a separate `targets_existing_request` service later completes it. `uchoice_inbound_request`/`uchoice_outbound_request` only. Added after a real bug: without this flag, `workflow_engine` marked these `'success'` the instant the customer confirmed, before any physical warehouse work happened |
| `created_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

### `workflow`
A named, reusable sequence of steps. One workflow can be shared by multiple `group_service` rows.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `workflow_id` | UUID | PK | `gen_random_uuid()` | Referenced by `group_service.workflow_id` and `workflow_step.workflow_id` |
| `name` | varchar(200) | NOT NULL, UNIQUE | — | Human-readable identifier (e.g. `"fedex_workorder"`) |
| `description` | text | nullable | — | What this workflow does, in plain language |
| `created_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

### `workflow_step`
One ordered step within a workflow. Executed synchronously, in `step_order`, all in one pass when the customer confirms — there is currently no mechanism to pause a workflow mid-sequence (e.g. to wait on a human action).

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `step_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `workflow_id` | UUID | NOT NULL, FK → `workflow.workflow_id` ON DELETE CASCADE | — | Which workflow this step belongs to |
| `step_order` | smallint | NOT NULL | — | Execution order within the workflow. `UNIQUE(workflow_id, step_order)` — no duplicate positions |
| `step_type` | varchar(100) | NOT NULL | — | Looked up in `handlers/registry.py` at runtime to find the Python handler class. **No DB-level constraint tying this to a real handler** — a typo here fails at runtime, not at insert time |
| `config` | JSONB | NOT NULL | `'{}'` | Step-specific static config (e.g. `{"carrier": "fedex"}`). Merged with `group_service.config` before being passed to the handler — `group_service.config` values win on key conflicts |

### `conversation_session`
One in-flight request. Ephemeral by design — represents "where a customer currently is" in a multi-turn conversation, not a permanent record.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `session_id` | UUID | PK | `gen_random_uuid()` | Internal identifier, referenced by `conversation_session.request_log_id` link (see below) |
| `wechat_openid` | varchar(128) | NOT NULL | — | Who this session belongs to |
| `group_id` | UUID | NOT NULL, FK → `group_config.group_id` ON DELETE CASCADE | — | Which group |
| `service_type_id` | UUID | nullable, FK → `service_type.service_type_id` ON DELETE SET NULL | — | NULL until the AI classifies the request via `new_request`; locked in from that point on |
| `status` | varchar(30) | NOT NULL | `"active"` | One of `active`, `pending_confirmation`, `completed`, `cancelled`, `rejected`, `failed`, `timed_out`. Only `active`/`pending_confirmation` count as "in progress" — that's what `session_manager.find_current_session()` filters on |
| `conversation_history` | JSONB (list) | NOT NULL | `'[]'` | Full `[{role, content}, ...]` turn history, sent to the AI on every call so it has conversational memory |
| `collected_fields` | JSONB (dict) | NOT NULL | `'{}'` | Accumulated `extracted_fields` from the AI across turns — this becomes `context["collected_fields"]` that handlers read from |
| `request_log_id` | UUID | nullable | — | Set as soon as the request_log row is created/resolved — at `new_request` time for ordinary services *(changed V3)*, or once the target is resolved for `targets_existing_request` services (points at the *target's* row, not a new one — see `service_type.targets_existing_request`). **Not a DB-level FK** (added via `ALTER TABLE` after `request_log` existed, to avoid a circular dependency at table-creation time — see Key Design Decisions) |
| `expires_at` | timestamptz | NOT NULL | `now() + 1 hour` | Reset on every new message (`session_manager.add_message()`); a background job (`jobs/session_expiry.py`, runs every 5 min) closes sessions past this and notifies the user |
| `created_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Audit timestamps |

### `request_log`
The permanent audit trail. **Created as soon as a service is resolved at `new_request` time** *(changed V3 — was "once `all_fields_collected = true`")*, status `pending`, so every resolved request is logged regardless of eventual outcome (confirmed, cancelled, or abandoned) — this also fixed a pre-existing bug where cancelling a request never touched `request_log` at all. Messages that never resolve to a service (unrecognized, access-denied) still leave no row here, only a `conversation_session` status change. Exception: `targets_existing_request` services never create their own row — they update the *target's* row instead.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `log_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `serial_number` | varchar(30) | NOT NULL, UNIQUE | `generate_serial_number()` (DB function) | Customer-facing reference, format `REQ-YYYYMMDD-000001` — global sequence, never resets, 6-digit padding. This is what customers quote back to the bot mid-conversation and what admins search by |
| `wechat_openid` | varchar(128) | NOT NULL | — | Who submitted the request |
| `group_id` | UUID | nullable, FK → `group_config.group_id` ON DELETE SET NULL | — | Which group. SET NULL (not CASCADE) so historical logs survive a group being deleted |
| `service_type_id` | UUID | nullable, FK → `service_type.service_type_id` ON DELETE SET NULL | — | Which service. Same SET NULL rationale |
| `status` | varchar(20) | NOT NULL | `"pending"` *(V5, was `"processing"`)* | Lifecycle (expanded V3): `pending` (awaiting customer confirm) → `processing` (confirmed, awaiting completion — momentary for single-shot services like FedEx, long-lived for U-Choice's two-step inbound/outbound flow) → `success` \| `failed` \| `cancelled` \| `timed_out` \| `stale` (a `processing` request that sat >7 days with no warehouse completion — retired daily by `jobs/uchoice_daily.py`) |
| `raw_message` | text | NOT NULL | — | The exact message that triggered `all_fields_collected` — kept verbatim for dispute resolution |
| `parsed_input` | JSONB | NOT NULL | `'{}'` | Snapshot of `collected_fields` at confirmation time |
| `result` | JSONB | nullable | — | Whatever the workflow's handlers returned — tracking number, label base64, OMS work order number, etc. Shape varies by service type; there is no fixed schema for this column |
| `error_detail` | text | nullable | — | Exception message if `status = failed` |
| `wechat_msg_id` | varchar(128) | nullable, UNIQUE | — | The WeChat message ID that triggered this log entry — doubles as a dedup safety net at the DB level, on top of the in-memory dedup in `api/webhook.py` |
| `created_at` | timestamptz | NOT NULL | `now()` | When the request was logged — now `new_request` time, not confirmation-trigger time (see note above) |
| `completed_at` | timestamptz | nullable | — | Set when status moves to a terminal state. `core/uchoice_invoice.py`'s `compute_invoice()` bills by this, not `created_at` — a request submitted July 31 but physically fulfilled August 2 belongs to August's invoice |

### `interaction_log` *(V3)*
Write-once, append-only. One row per incoming message once intent is classified, **regardless of outcome** (including small talk and rejected/unrecognized messages) — separate from `request_log`, which only covers requests that resolved to an actual service. Purpose: funnel/efficiency analysis ("what fraction of a group's traffic becomes real work") without the stricter lookup/update semantics `request_log` needs.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `interaction_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `wechat_openid` | varchar(128) | NOT NULL | — | Who sent the message |
| `group_id` | UUID | nullable, FK → `group_config.group_id` ON DELETE SET NULL | — | Which group |
| `intent` | varchar(30) | NOT NULL | — | The AI's raw classification (`new_request`, `continuation`, `confirm`, `cancel`, `check_services`, `unrecognized`), plus `rejected` (set by the workflow engine, not the AI, when access control or service lookup fails) |
| `intent_type` | varchar(20) | NOT NULL | — | Derived category for cheap `GROUP BY` analysis: `new_request`/`continuation`/`confirm` → `productive`, `cancel` → `abandoned`, `check_services` → `informational`, `unrecognized` → `noise`, `rejected` → `denied` |
| `service_type_id` | UUID | nullable, FK → `service_type.service_type_id` ON DELETE SET NULL | — | Which service, if resolved |
| `request_log_id` | UUID | nullable, FK → `request_log.log_id` ON DELETE SET NULL | — | Linked request, if one was created |
| `created_at` | timestamptz | NOT NULL | `now()` | When |

### `uchoice_sku` *(V3)*
Catalog of the 8 real U-Choice products (stretch wrap, packing tape) — excludes `uchoice_plt` (通用托盘, a unit-of-measure helper, not a trackable/shippable product).

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `sku_code` | varchar(50) | PK | — | e.g. `s1`, `t4` — referenced by `uchoice_storage`, `uchoice_storage_txn`, and every SKU-carrying `request_log.result`/`collected_fields` |
| `description` | varchar(200) | NOT NULL | — | Human-readable product name, e.g. "T4 2-inch Clear Packing Tape". This is what every confirmation/response builder resolves `sku_code` to via `core/uchoice_context.py`'s `sku_label_map()` — a raw `sku_code` should never reach a customer's screen |

### `uchoice_storage` *(V3)* — **no `group_id`**
Current balance per `(warehouse, sku, boxes_per_pallet)` bucket.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `warehouse_code` | varchar(20) | PK (1/3) | — | `JFK` or `DE` — the only two warehouses |
| `sku_code` | varchar(50) | PK (2/3), FK → `uchoice_sku.sku_code` | — | Which product |
| `boxes_per_pallet` | integer | PK (3/3) | — | **A free integer, not a pre-registered catalog value.** Buckets are created dynamically the first time a given box-count occurs — real box counts drift from ad-hoc partial picks (an 80-box pallet becomes a 77-box pallet after a 3-box pick), so there's no fixed enumeration of valid configs |
| `pallet_count` | integer | NOT NULL, `CHECK (pallet_count >= 0)` | `0` | Current balance. The CHECK is the negative-balance safety net — `core/uchoice_storage.py`'s `apply_storage_delta()` catches the resulting DB error and turns it into a clean "库存不足" message rather than leaking the raw constraint error |
| `updated_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

### `uchoice_storage_txn` *(V3)* — **no `group_id`**
Audit log — every mutation to `uchoice_storage` writes exactly one row here, via the shared `apply_storage_delta()` (`core/uchoice_storage.py`) — no other code path writes to either table.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `txn_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `warehouse_code` / `sku_code` / `boxes_per_pallet` | — | NOT NULL | — | Which bucket this transaction affected |
| `pallet_delta` | integer | NOT NULL | — | Signed change applied |
| `txn_type` | varchar(20) | NOT NULL, `CHECK IN (...)` | — | `inbound`, `outbound`, `convert_in`/`convert_out` (customer-fulfillment loose-box picks), `move_in`/`move_out` (internal warehouse repackaging — same arithmetic as convert, deliberately distinct type so `view_storage_history` can tell them apart), `adjust`, `recount` |
| `request_log_id` | UUID | nullable, FK → `request_log.log_id` ON DELETE SET NULL | — | Which request caused this, if any |
| `note` | text | nullable | — | Free text, e.g. an `adjust_storage` reason |
| `created_by` | varchar(128) | NOT NULL | — | `wechat_openid` of whoever triggered it |
| `created_at` | timestamptz | NOT NULL | `now()` | When |

### `uchoice_address` *(V3)* — **no `group_id`**
Shared, company-wide address book — outbound shipping destinations and inter-warehouse transfer addresses.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `address_id` | UUID | PK | `gen_random_uuid()` | Referenced by `uchoice_outbound_request.destination_address_id` |
| `company_name` | varchar(200) | NOT NULL | — | Shown resolved (not the raw `address_id`) in every confirmation/response — this was a real bug, fixed after the address side of `uchoice_outbound_request`'s confirmation was found showing a raw UUID |
| `charge_type` | varchar(20) | NOT NULL, `CHECK IN ('short_delivery','delivery','truck_transfer')` | — | Delivery method tier, not distance — renamed from an initial `distance_tier` naming. Always shown with its rate via `core/confirmation.py`'s `charge_type_label()`, e.g. "卡车转仓（$85）" |
| `addr` | text | NOT NULL | — | Free-text address |
| `warehouse_code` | varchar(20) | **required as of V7** (enforced at the `input_schema` level, not a DB NOT NULL) | — | Which warehouse this address is associated with — every address, not just `truck_transfer` ones, per explicit decision |
| `note` | text | nullable | — | Free text, e.g. a nickname |
| `created_by` | varchar(128) | NOT NULL | — | Who created/last updated it |
| `created_at` | timestamptz | NOT NULL | `now()` | Audit timestamp |

### `uchoice_storage_fee_ledger` *(V3)* — **no `group_id`**
One row per warehouse per day, populated by `jobs/uchoice_daily.py`'s daily job — sums `uchoice_storage.pallet_count` across all SKUs for a warehouse × `$1/pallet/day`.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `ledger_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `warehouse_code` | varchar(20) | NOT NULL | — | Which warehouse |
| `fee_date` | date | NOT NULL | — | Which day. `UNIQUE(warehouse_code, fee_date)` — one snapshot per warehouse per day, upserted if the job reruns |
| `pallet_count` | integer | NOT NULL | — | Total pallets across all SKUs that day |
| `storage_fee` | numeric(10,2) | NOT NULL | — | `pallet_count × $1`. Summed by `core/uchoice_invoice.py`'s `compute_invoice()` for the storage-fee line item |

---

## Security Notes (current state, not yet hardened)

- **`group_service.config` is plaintext JSONB.** Per-group API credentials (YiDiDa keys, OMS App_Key/Secret, warehouse codes) are stored and returned by the admin API with no application-level encryption. Anyone with the admin API key can read them back verbatim via `GET /admin/groups/{id}/services`.
- **Platform secrets are environment variables** (`config.py`) — not committed to git, loaded via Render's dashboard in production. Reasonable for a solo operator; not yet hardened for a multi-admin team (no per-admin credentials, no rotation).
- **`ADMIN_API_KEY` is a single static bearer token** (`middleware/admin_auth.py`) — no expiry, no per-admin identity, can't revoke one admin's access without rotating the key for everyone.
- **Backlog item:** encrypt `group_service.config` at the application layer (e.g. Fernet symmetric encryption, key from a dedicated env var, decrypt only inside handlers immediately before use) before onboarding any customer whose credentials you don't already control end-to-end.

---

## Migration Files

| File | Purpose | Run order |
|---|---|---|
| `db/migrations/V1__initial_schema.sql` | Creates all 10 base-platform tables, indexes, constraints | 1st |
| `db/migrations/V2__seed_catalog.sql` | Seeds `role` (`admin`, `customer`), `service_type` (`fedex_label`, `ups_label`), `workflow`/`workflow_step` (`fedex_workorder`, `ups_only`) — the global catalog only, no group-specific data | 2nd |
| `db/migrations/V3__uchoice_catalog.sql` | The whole U-Choice pipeline: `service_type`/`group_member`/`group_config`/`request_log` schema changes, `interaction_log` + all 5 `uchoice_*` tables, `warehouseman`/`accountant` roles, SKU catalog seed, inter-warehouse address seed, 12 U-Choice `service_type` rows + workflows | 3rd |
| `db/migrations/V4__request_lifecycle_fix.sql` | Adds `service_type.awaits_completion` — fixes a real bug found via live testing (see that column's entry above) | 4th |
| `db/migrations/V5__completion_flow_fixes.sql` | `reference_serial` required for both completion services; translates all 9 English `confirmation_note` values to Chinese | 5th |
| `db/migrations/V6__storage_history_range.sql` | `view_storage_history`: `target_month` → `start_month`/`end_month`, multi-month range support | 6th |
| `db/migrations/V7__address_warehouse_required.sql` | `uchoice_address.warehouse_code` required for every address | 7th |
| `db/migrations/V8__invoice_range.sql` | `view_invoice`: same range change as V6 | 8th |

**Consolidated 2026-08-03 (V1/V2).** The original history (V1 → V7, built incrementally through Phase 6 testing) accumulated real churn worth knowing about if you ever need to reconstruct it from git: a duplicate `V4__*.sql` filename from a dead-end column-add attempt, an `input_schema` that was seeded wrong in V2 and rewritten in V4, a two-service `fedex_label`/`fedex_oms_label` split later merged into one service with `oms_outbound_order_no` as an optional field, and a `group_member.role` string column migrated to a `role_id` FK via an add-column/backfill/drop-column dance (only necessary because it ran against live data). None of that history carries forward past that point.

**V3 onward were all found and fixed through actually building and testing the U-Choice pipeline** (not a second consolidation) — several (V4, V5, V6/V8, V7) are direct fixes for gaps found via live testing or user review after V3 shipped. This is the expected pattern going forward: real bugs get their own small migration, not a rewrite of the one that introduced them.

Group-specific setup (registering a group, adding members with roles, assigning services with credentials, granting service-role permissions) is **not** in any migration file — it's done live via the Admin API onboarding flow. See `docs/ops/admin-api-reference.md` → "Typical Onboarding Flow".

**Adding new service types or workflows in future versions:** see `docs/ops/adding-a-service.md` for the full process, not just the migration step. Short version: create a new numbered file, never edit an existing one after it's been applied to a real database.
