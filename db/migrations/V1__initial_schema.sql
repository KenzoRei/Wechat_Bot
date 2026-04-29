-- ============================================================
-- V1: Initial Schema
-- Logistics WeChat Bot Platform
-- Date: 2026-04-26
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ── group_config ──────────────────────────────────────────────────────────────
-- Each WeChat Work group the bot serves.
CREATE TABLE group_config (
    group_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wechat_group_id     VARCHAR(128) NOT NULL UNIQUE,  -- WeChat's group ID from webhook payload
    description         VARCHAR(500),                  -- human label e.g. "NYC Customer Group A"
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    daily_request_limit INTEGER,                       -- NULL = unlimited; set to cap group's daily total
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── group_member ──────────────────────────────────────────────────────────────
-- Who belongs to each group and their role.
-- Bot silently ignores anyone NOT in this table.
CREATE TABLE group_member (
    wechat_openid   VARCHAR(128) NOT NULL,          -- WeChat's permanent internal user ID
    group_id        UUID NOT NULL REFERENCES group_config(group_id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'customer')),
    display_name    VARCHAR(200),                   -- for notifications and logs
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,  -- suspend without removing
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (wechat_openid, group_id)           -- one user can be in multiple groups
);

CREATE INDEX idx_group_member_group  ON group_member(group_id);
CREATE INDEX idx_group_member_openid ON group_member(wechat_openid);

-- ── service_type ──────────────────────────────────────────────────────────────
-- Each service the platform supports.
-- input_schema tells Claude what fields to collect from users.
-- group_config_schema defines what config the admin must supply per group.
CREATE TABLE service_type (
    service_type_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(100) NOT NULL UNIQUE,   -- e.g. "fedex_label", "rate_quote"
    description         VARCHAR(500),
    input_schema        JSONB NOT NULL DEFAULT '{}',    -- required/optional fields + hints for Claude
    group_config_schema JSONB NOT NULL DEFAULT '{}',    -- required/optional keys for group_service.config
                                                        -- validated by API on POST /admin/groups/{id}/services
    confirmation_note   TEXT,                           -- optional disclaimer shown at bottom of confirmation template
                                                        -- NULL = no note shown for this service
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── workflow ──────────────────────────────────────────────────────────────────
-- A named sequence of steps. Multiple groups can share one workflow.
CREATE TABLE workflow (
    workflow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(200) NOT NULL UNIQUE,       -- e.g. "fedex_with_oms", "fedex_only"
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── workflow_step ─────────────────────────────────────────────────────────────
-- Ordered steps inside a workflow. Each step maps to one handler.
CREATE TABLE workflow_step (
    step_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflow(workflow_id) ON DELETE CASCADE,
    step_order  SMALLINT NOT NULL,                  -- execution order: 1, 2, 3...
    step_type   VARCHAR(100) NOT NULL,              -- maps to handler registry key
                                                    -- e.g. "create_fedex_label", "oms_record"
    config      JSONB NOT NULL DEFAULT '{}',        -- handler-specific params per group
    UNIQUE (workflow_id, step_order)                -- no duplicate order within a workflow
);

CREATE INDEX idx_workflow_step ON workflow_step(workflow_id, step_order);

-- ── group_service ─────────────────────────────────────────────────────────────
-- Which services each group can use, and which workflow runs for each.
-- workflow_id lives HERE (not in service_type) to support per-group workflow variation.
-- config holds group-specific API params (e.g. YDD customer ID, channel ID).
-- config keys are validated against service_type.group_config_schema on write.
-- e.g. Group A: fedex_label → fedex_with_oms, config: {ydd_cust_id: "A001"}
--      Group B: fedex_label → fedex_only,     config: {ydd_cust_id: "B001"}
CREATE TABLE group_service (
    group_id        UUID NOT NULL REFERENCES group_config(group_id) ON DELETE CASCADE,
    service_type_id UUID NOT NULL REFERENCES service_type(service_type_id) ON DELETE CASCADE,
    workflow_id     UUID NOT NULL REFERENCES workflow(workflow_id) ON DELETE RESTRICT,
    config          JSONB NOT NULL DEFAULT '{}',   -- group-specific API credentials/params
    PRIMARY KEY (group_id, service_type_id)
);

CREATE INDEX idx_group_service_group ON group_service(group_id);

-- ── Serial number generator ───────────────────────────────────────────────────
-- Produces human-readable serial numbers: REQ-YYYYMMDD-000001
-- Global sequence (does not reset daily). 6-digit padding = 999,999 requests.
CREATE SEQUENCE request_serial_seq START 1;

CREATE OR REPLACE FUNCTION generate_serial_number()
RETURNS VARCHAR AS $$
BEGIN
    RETURN 'REQ-' ||
           TO_CHAR(now(), 'YYYYMMDD') || '-' ||
           LPAD(nextval('request_serial_seq')::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- ── conversation_session ──────────────────────────────────────────────────────
-- Active multi-turn conversations. Temporary — closed after completion or expiry.
-- Equivalent to RAM: holds state during collection, released when done.
CREATE TABLE conversation_session (
    session_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wechat_openid        VARCHAR(128) NOT NULL,
    group_id             UUID NOT NULL REFERENCES group_config(group_id) ON DELETE CASCADE,
    service_type_id      UUID REFERENCES service_type(service_type_id) ON DELETE SET NULL,
                                                    -- nullable: set once Claude classifies
    status               VARCHAR(30) NOT NULL DEFAULT 'active'
                         CHECK (status IN (
                             'active',              -- collecting fields from user
                             'pending_confirmation',-- bot sent confirmation template, waiting for user
                             'completed',           -- user confirmed, workflow finished successfully
                             'cancelled',           -- user explicitly cancelled
                             'rejected',            -- user asked for service their group doesn't have
                             'timed_out',           -- expired after 1 hour of no activity
                             'failed'               -- system/API error mid-workflow
                         )),
    conversation_history JSONB NOT NULL DEFAULT '[]',  -- full message history for Claude
    collected_fields     JSONB NOT NULL DEFAULT '{}',  -- fields gathered so far
    request_log_id       UUID,                         -- FK added below after request_log
    expires_at           TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '1 hour',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enforces one in-progress session per user per group at any time.
-- Covers both active (collecting) and pending_confirmation (awaiting confirm).
CREATE UNIQUE INDEX idx_session_one_active_per_user
    ON conversation_session(wechat_openid, group_id)
    WHERE status IN ('active', 'pending_confirmation');

-- Background job uses this to efficiently find expired sessions.
-- Covers both statuses — a pending_confirmation session can also time out.
CREATE INDEX idx_session_expires
    ON conversation_session(expires_at)
    WHERE status IN ('active', 'pending_confirmation');

CREATE INDEX idx_session_openid ON conversation_session(wechat_openid);

-- ── request_log ───────────────────────────────────────────────────────────────
-- Permanent record of every submitted request.
-- Equivalent to hard disk: written at confirmation, never deleted.
CREATE TABLE request_log (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serial_number   VARCHAR(30) NOT NULL DEFAULT generate_serial_number(),
    wechat_openid   VARCHAR(128) NOT NULL,           -- who made the request
    group_id        UUID REFERENCES group_config(group_id) ON DELETE SET NULL,
    service_type_id UUID REFERENCES service_type(service_type_id) ON DELETE SET NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'processing'
                    CHECK (status IN (
                        'processing',               -- workflow running
                        'success',                  -- completed successfully
                        'failed',                   -- workflow error
                        'timed_out'                 -- backend timeout
                    )),
    raw_message     TEXT NOT NULL,                  -- user's original first message
    parsed_input    JSONB NOT NULL DEFAULT '{}',    -- all fields after AI normalization
    result          JSONB,                          -- e.g. tracking number, label URL
    error_detail    TEXT,                           -- error message for admin debugging
    wechat_msg_id   VARCHAR(128),                   -- WeChat message ID for idempotency
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ                     -- set when workflow finishes
);

CREATE UNIQUE INDEX idx_request_log_serial
    ON request_log(serial_number);

CREATE UNIQUE INDEX idx_request_log_msg_id
    ON request_log(wechat_msg_id)
    WHERE wechat_msg_id IS NOT NULL;                -- idempotency: reject duplicate webhooks

CREATE INDEX idx_request_log_openid  ON request_log(wechat_openid);
CREATE INDEX idx_request_log_group   ON request_log(group_id);
CREATE INDEX idx_request_log_status  ON request_log(status);
CREATE INDEX idx_request_log_created ON request_log(created_at DESC);

-- ── FK: conversation_session → request_log ────────────────────────────────────
-- Added after request_log exists. Links session to its submitted request.
ALTER TABLE conversation_session
    ADD CONSTRAINT fk_session_request_log
    FOREIGN KEY (request_log_id)
    REFERENCES request_log(log_id)
    ON DELETE SET NULL;
