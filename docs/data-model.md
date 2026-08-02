# Data Model
# Logistics WeChat Bot Platform — v1

**Version:** 1.0
**Date:** 2026-04-26
**Status:** Finalized

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

---

## Key Design Decisions

| Decision | Detail |
|---|---|
| `workflow_id` lives in `group_service` | Same service type can run different workflows per group — e.g. Group A: FedEx + OMS, Group B: FedEx only |
| `daily_request_limit` in `group_config` | Per-group daily request cap. NULL = unlimited. Checked in Access Control before passing to Claude. V2 will enforce it; column added now to avoid a migration later. |
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
| `created_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Standard audit timestamps |

### `group_member`
Who can talk to the bot in a given group, and what role they hold there. Composite PK — see note below.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `wechat_openid` | varchar(128) | PK (1/2) | — | WeChat user ID — the `from_user` field on every incoming message |
| `group_id` | UUID | PK (2/2), FK → `group_config.group_id` ON DELETE CASCADE | — | Which group this membership row applies to |
| `role_id` | UUID | NOT NULL, FK → `role.role_id` ON DELETE RESTRICT | — | Which role this member holds in this group. Resolved to a role *name* string (e.g. `"admin"`) before being loaded into the AI prompt context and into `AccessResult.role` |
| `display_name` | varchar(200) | nullable | — | Name shown in bot replies, confirmation templates, and request logs. Scoped per-group deliberately — same person can have a different display name in different groups |
| `is_active` | boolean | NOT NULL | `true` | Suspended members get a permission-denied reply instead of being silently ignored |
| `joined_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Audit timestamps |

**Note on the composite key:** PK is `(wechat_openid, group_id)` only — `role_id` is not part of the key. This means one user has exactly **one** row, and therefore **one** role, per group at any given time. The same user can hold different roles across different groups (separate rows, separate `group_id`), but cannot hold two roles simultaneously within one group.

`ON DELETE RESTRICT` on `role_id` is deliberate: a role can't be deleted from the `role` table while any `group_member` still holds it — forces an explicit reassignment first, rather than silently orphaning members.

### `role`
Catalog of role names. Introduced in V7 to replace the hardcoded `VALID_ROLES = {"admin", "customer"}` set that previously lived in `api/admin/members.py` — new roles (e.g. `"warehouseman"`, `"accountant"` for U-Choice) are now added via `POST /admin/roles`, no redeploy required.

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
**Deny-by-default permission grant**, introduced in V7. A `(group_id, service_type_id)` pair assigned via `group_service` is invisible to a role unless a matching row exists here — no exceptions, no implicit admin bypass at the query level (admin access works because every existing `group_service` row is explicitly granted to the `admin` role at migration time, not because of any special-casing in code).

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
| `confirmation_note` | text | nullable | — | Optional disclaimer appended to the confirmation message shown to the customer before they confirm (e.g. billing terms) |
| `is_active` | boolean | NOT NULL | `true` | Inactive service types are excluded from `/admin/service-types` and from `allowed_services` in access control |
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
| `request_log_id` | UUID | nullable | — | Set once `_trigger_confirmation()` fires — links forward to the permanent `request_log` row created at that point. **Not a DB-level FK** (added via `ALTER TABLE` after `request_log` existed, to avoid a circular dependency at table-creation time — see Key Design Decisions) |
| `expires_at` | timestamptz | NOT NULL | `now() + 1 hour` | Reset on every new message (`session_manager.add_message()`); a background job (`jobs/session_expiry.py`, runs every 5 min) closes sessions past this and notifies the user |
| `created_at` / `updated_at` | timestamptz | NOT NULL | `now()` | Audit timestamps |

### `request_log`
The permanent audit trail. **Only created once a request reaches `all_fields_collected = true`** — messages that never get that far (unrecognized, access-denied, abandoned mid-collection) leave no row here at all, only a `conversation_session` status change.

| Column | Type | Null? | Default | Purpose |
|---|---|---|---|---|
| `log_id` | UUID | PK | `gen_random_uuid()` | Internal identifier |
| `serial_number` | varchar(30) | NOT NULL, UNIQUE | `generate_serial_number()` (DB function) | Customer-facing reference, format `REQ-YYYYMMDD-000001` — global sequence, never resets, 6-digit padding. This is what customers quote back to the bot mid-conversation and what admins search by |
| `wechat_openid` | varchar(128) | NOT NULL | — | Who submitted the request |
| `group_id` | UUID | nullable, FK → `group_config.group_id` ON DELETE SET NULL | — | Which group. SET NULL (not CASCADE) so historical logs survive a group being deleted |
| `service_type_id` | UUID | nullable, FK → `service_type.service_type_id` ON DELETE SET NULL | — | Which service. Same SET NULL rationale |
| `status` | varchar(20) | NOT NULL | `"processing"` | `processing` → `success` \| `failed` \| `timed_out` |
| `raw_message` | text | NOT NULL | — | The exact message that triggered `all_fields_collected` — kept verbatim for dispute resolution |
| `parsed_input` | JSONB | NOT NULL | `'{}'` | Snapshot of `collected_fields` at confirmation time |
| `result` | JSONB | nullable | — | Whatever the workflow's handlers returned — tracking number, label base64, OMS work order number, etc. Shape varies by service type; there is no fixed schema for this column |
| `error_detail` | text | nullable | — | Exception message if `status = failed` |
| `wechat_msg_id` | varchar(128) | nullable, UNIQUE | — | The WeChat message ID that triggered this log entry — doubles as a dedup safety net at the DB level, on top of the in-memory dedup in `api/webhook.py` |
| `created_at` | timestamptz | NOT NULL | `now()` | When the request was logged (confirmation-trigger time, not submission time) |
| `completed_at` | timestamptz | nullable | — | Set when status moves to a terminal state |

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
| `db/migrations/V1__initial_schema.sql` | Creates all tables, indexes, constraints | 1st |
| `db/migrations/V2__seed_data.sql` | Inserts service types, workflows, workflow steps | 2nd |
| `db/migrations/V4__update_schema_and_group_context.sql` | Updates input_schema (shipper fields, optional service_level, country); adds `context` column to `group_config`; sets LAX/DE presets for test group | 3rd |
| `db/migrations/V5__update_ydd_credentials_hint.sql` | Updates YiDiDa field hints; sets real test-group YiDiDa credentials | 4th |
| `db/migrations/V6__oms_service_type.sql` | Adds `fedex_oms_label` service type, `fedex_workorder` workflow + steps, updates test group service assignments with OMS credentials | 5th |
| `db/migrations/V7__role_permission_model.sql` | Adds `role` table (replaces hardcoded `VALID_ROLES`), migrates `group_member.role` → `role_id` FK, adds `group_service_role` (deny-by-default service permission grants), backfills admin-role access to every existing `group_service` row | 6th |

Note: V3 was deleted during development (superseded by V4) — there is no `V3__*.sql` file, this is expected.

On Render, V6 and V7 were applied via idempotent admin endpoints (`api/admin/seed_v6.py`, `api/admin/migrate_v7.py`) rather than a raw `psql` run, since the Render DB isn't directly reachable from a local shell without the external connection string. Both use conflict-safe / existence-checked logic so they're safe to re-run.

**Adding new service types or workflows in future versions:**
Create a new numbered file — `V8__add_rate_quote.sql`, `V9__add_warehouse_in.sql`, etc.
Never edit existing migration files after deployment.
