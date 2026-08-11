\encoding UTF8
-- ============================================================
-- V8: WeChat Kefu staff-facing migration -- data model
-- Logistics WeChat Bot Platform
-- Date: 2026-08-11
--
-- Implements docs/ai-collaboration/kefu-migration-plan.md Sec 2 (signed
-- v7, Claude Code round 77 / Codex round 78, user-approved round 79).
-- Claude Code's single-writer scope per the plan's Sec 12 work division:
-- all migrations/models. Codex's transport/worker code (Sec 5/6.1/11.3)
-- is separate and does not touch this file.
--
-- Additive only. Smart Robot's existing behavior is unaffected -- every
-- new column on an existing table is nullable or has a default matching
-- current behavior (source_channel defaults to 'smart_robot').
-- ============================================================

-- ── 2.1 uchoice_customer ─────────────────────────────────────

CREATE TABLE uchoice_customer (
    customer_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code  VARCHAR(30) NOT NULL UNIQUE,
    canonical_name VARCHAR(200) NOT NULL,
    aliases        TEXT[] NOT NULL DEFAULT '{}',
    is_active      BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 2.2 uchoice_address.customer_id ──────────────────────────
-- Nullable during migration -- backfill/classification happens in a
-- separate data-migration step (Claude Code task, not this DDL file),
-- then a follow-up migration tightens this to NOT NULL once the 5
-- null-company_name rows are manually classified (plan Sec 2.2).

ALTER TABLE uchoice_address
    ADD COLUMN customer_id UUID REFERENCES uchoice_customer(customer_id);

-- ── 2.3 kefu_staff ────────────────────────────────────────────

CREATE TABLE kefu_staff (
    staff_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    open_kfid      VARCHAR(128) NOT NULL,
    external_userid VARCHAR(128) NOT NULL,
    group_id       UUID NOT NULL REFERENCES group_config(group_id) ON DELETE RESTRICT,
    role_id        UUID NOT NULL REFERENCES role(role_id) ON DELETE RESTRICT,
    warehouse_code VARCHAR(20),
    display_name   VARCHAR(200),
    is_active      BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- group_id deliberately excluded from the uniqueness key -- plan Sec
    -- 2.3: one open_kfid maps to exactly one group_id for this migration,
    -- fixed at deployment time, so this identity is globally unique per
    -- Kefu account.
    UNIQUE (open_kfid, external_userid)
);

-- ── 2.4 request_log / interaction_log -- actor/requester split ──

ALTER TABLE request_log
    ALTER COLUMN wechat_openid DROP NOT NULL,
    ADD COLUMN customer_id           UUID REFERENCES uchoice_customer(customer_id),
    ADD COLUMN submitted_by_staff_id UUID REFERENCES kefu_staff(staff_id),
    ADD COLUMN source_channel        VARCHAR(20) NOT NULL DEFAULT 'smart_robot'
               CHECK (source_channel IN ('smart_robot', 'kefu')),
    ADD COLUMN origin_session_id     UUID REFERENCES conversation_session(session_id);

ALTER TABLE interaction_log
    ALTER COLUMN wechat_openid DROP NOT NULL,
    ADD COLUMN customer_id           UUID REFERENCES uchoice_customer(customer_id),
    ADD COLUMN submitted_by_staff_id UUID REFERENCES kefu_staff(staff_id),
    ADD COLUMN source_channel        VARCHAR(20) NOT NULL DEFAULT 'smart_robot'
               CHECK (source_channel IN ('smart_robot', 'kefu'));

-- ── 2.5 conversation_session -- channel identity, case number ───

-- Case-number generator, same pattern as generate_serial_number() (V1),
-- distinct prefix and sequence so a case number is never confused with a
-- request serial number.
CREATE SEQUENCE case_serial_seq;

CREATE FUNCTION generate_case_number() RETURNS VARCHAR
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN 'CASE-' ||
           TO_CHAR(now(), 'YYYYMMDD') || '-' ||
           LPAD(nextval('case_serial_seq')::TEXT, 6, '0');
END;
$$;

ALTER TABLE conversation_session
    ALTER COLUMN wechat_openid DROP NOT NULL,
    ADD COLUMN source_channel     VARCHAR(20) NOT NULL DEFAULT 'smart_robot'
               CHECK (source_channel IN ('smart_robot', 'kefu')),
    ADD COLUMN opened_by_staff_id UUID REFERENCES kefu_staff(staff_id),
    ADD COLUMN case_revision      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN customer_id        UUID REFERENCES uchoice_customer(customer_id),
    ADD COLUMN case_number        VARCHAR(30) UNIQUE;

-- ── 2.5 case_turn -- durable per-turn actor audit ────────────────

CREATE TABLE case_turn (
    turn_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES conversation_session(session_id) ON DELETE CASCADE,
    case_revision       INTEGER NOT NULL,
    acting_staff_id      UUID REFERENCES kefu_staff(staff_id),
    acting_wechat_openid VARCHAR(128),
    role                 VARCHAR(20) NOT NULL,
    source_message_id     VARCHAR(128) UNIQUE,
    reply_text             TEXT,
    customer_copy_text      TEXT,
    artifact_keys            TEXT[],
    content                  TEXT NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (role = 'user' AND num_nonnulls(acting_staff_id, acting_wechat_openid) = 1)
        OR
        (role = 'assistant' AND acting_staff_id IS NULL AND acting_wechat_openid IS NULL)
    )
);

CREATE INDEX idx_case_turn_session ON case_turn(session_id);

-- ── 2.5 case_execution -- durable execution ledger ───────────────

CREATE TABLE case_execution (
    execution_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID NOT NULL REFERENCES conversation_session(session_id) ON DELETE CASCADE,
    execution_key    VARCHAR(128) NOT NULL UNIQUE,
    status           VARCHAR(20) NOT NULL DEFAULT 'claimed'
                     CHECK (status IN ('claimed', 'db_committed', 'completed', 'failed')),
    claimed_by       VARCHAR(128) NOT NULL,
    claimed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    heartbeat_at     TIMESTAMPTZ,
    db_committed_at  TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    last_error       TEXT
);

CREATE INDEX idx_case_execution_session ON case_execution(session_id);

-- ── 2.5 kefu_staff_case_context -- staff -> current-case binding ─

CREATE TABLE kefu_staff_case_context (
    staff_id          UUID PRIMARY KEY REFERENCES kefu_staff(staff_id) ON DELETE CASCADE,
    active_session_id UUID REFERENCES conversation_session(session_id) ON DELETE SET NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 2.6 Kefu transport/sync state ────────────────────────────────
-- Schema owned by Claude Code (plan Sec 12: "all migrations/models");
-- the receiver/worker/transport code that reads and writes these tables
-- is Codex's separate single-writer scope (plan Sec 5/6.1/11.3).

CREATE TABLE kefu_sync_cursor (
    open_kfid  VARCHAR(128) PRIMARY KEY,
    cursor     VARCHAR(128) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE kefu_inbound_message (
    msgid            VARCHAR(128) PRIMARY KEY,
    open_kfid        VARCHAR(128) NOT NULL,
    external_userid  VARCHAR(128) NOT NULL,
    payload          JSONB NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at     TIMESTAMPTZ,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'claimed', 'processed', 'failed')),
    claimed_by       VARCHAR(128),
    claimed_at       TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT
);

CREATE INDEX idx_kefu_inbound_message_identity_claim
    ON kefu_inbound_message(open_kfid, external_userid, status, received_at);

CREATE TABLE kefu_outbound_delivery (
    delivery_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID REFERENCES conversation_session(session_id),
    request_log_id          UUID REFERENCES request_log(log_id),
    recipient_staff_id       UUID NOT NULL REFERENCES kefu_staff(staff_id),
    idempotency_key           VARCHAR(128) NOT NULL UNIQUE,
    payload_type              VARCHAR(10) NOT NULL CHECK (payload_type IN ('text', 'file')),
    text_content                TEXT,
    artifact_request_log_id      UUID REFERENCES request_log(log_id),
    artifact_doc_type            VARCHAR(50),
    artifact_key                  VARCHAR(200),
    payload_hash                   VARCHAR(64) NOT NULL,
    provider_message_id             VARCHAR(128),
    status                           VARCHAR(20) NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'sent', 'failed')),
    attempt_count                    INTEGER NOT NULL DEFAULT 0,
    next_retry_at                     TIMESTAMPTZ,
    sent_at                            TIMESTAMPTZ,
    last_error                          TEXT,
    created_at                           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(session_id, request_log_id) = 1),
    CHECK (
        (payload_type = 'text' AND text_content IS NOT NULL AND artifact_request_log_id IS NULL)
        OR
        (payload_type = 'file' AND text_content IS NULL
            AND artifact_request_log_id IS NOT NULL
            AND artifact_doc_type IS NOT NULL
            AND artifact_key IS NOT NULL)
    )
);

CREATE INDEX idx_kefu_outbound_delivery_recipient_pending
    ON kefu_outbound_delivery(recipient_staff_id, status);
