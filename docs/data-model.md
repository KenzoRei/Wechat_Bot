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
        varchar role
        varchar display_name
        boolean is_active
        timestamptz joined_at
        timestamptz updated_at
    }

    group_service {
        uuid group_id PK_FK
        uuid service_type_id PK_FK
        uuid workflow_id FK
        jsonb config
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

    conversation_session ||--o| request_log : "becomes"
```

---

## Table Summary

| Table | Purpose | Type |
|---|---|---|
| `group_config` | Each WeChat Work group | Config |
| `group_member` | Who is in each group + role | Config |
| `group_service` | Which services + which workflow per group | Config |
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

## Migration Files

| File | Purpose | Run order |
|---|---|---|
| `db/migrations/V1__initial_schema.sql` | Creates all tables, indexes, constraints | 1st |
| `db/migrations/V2__seed_data.sql` | Inserts service types, workflows, workflow steps | 2nd |
| `db/migrations/V3__update_input_schema.sql` | Updates input_schema for fedex_label and ups_label — adds shipper fields, makes service_level optional | 3rd |

**Adding new service types or workflows in future versions:**
Create a new numbered file — `V3__add_rate_quote.sql`, `V4__add_warehouse_in.sql`, etc.
Never edit existing migration files after deployment.
