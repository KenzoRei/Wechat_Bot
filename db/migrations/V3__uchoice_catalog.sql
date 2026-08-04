\encoding UTF8
-- ============================================================
-- V3: U-Choice Catalog — Platform Schema Changes + U-Choice Tables + Seed Catalog
-- Logistics WeChat Bot Platform
-- Date: 2026-08-04
--
-- Source of truth for everything in this file: docs/uchoice-design.md
-- (design locked, this migration implements it as-is).
--
-- Global, group-agnostic catalog only, same as V2 — group-specific setup
-- (groups, members, credentials, service-role grants, warehouse assignment)
-- is done live via the Admin API, not seeded here.
-- ============================================================


-- ── Platform-level ALTERs ────────────────────────────────────────────────────

ALTER TABLE service_type
    ADD COLUMN requires_confirmation     BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN targets_existing_request  BOOLEAN NOT NULL DEFAULT FALSE;
-- requires_confirmation=false: workflow executes immediately once
--   all_fields_collected fires, no confirm/cancel template shown.
-- targets_existing_request=true: the service locates and updates an existing
--   request_log row (by reference_serial) instead of creating its own.

ALTER TABLE group_member
    ADD COLUMN warehouse_code VARCHAR(20);
-- Nullable; meaningful only for role=warehouseman. Required-for-that-role and
-- cleared-on-role-change-away are enforced at the API layer (api/admin/members.py),
-- not a DB CHECK — a CHECK can't reach across the FK to know the role's name.

ALTER TABLE group_config
    ADD COLUMN group_robot_webhook_url TEXT;
-- WeChat Work Group Robot Webhook (静态, persistent per-group URL, unlike the
-- single-use response_url). Used for scheduled/proactive pushes — daily
-- broadcast, monthly invoice, cross-group completion notifications.

-- request_log.status: expand the CHECK constraint and change the default.
-- Old: processing, success, failed, timed_out.
-- New: pending (renamed meaning of old default — awaiting customer confirm),
--      processing (reused — confirmed, awaiting completion),
--      success, failed, cancelled (new), timed_out, stale (new).
-- Constraint name isn't declared in V1 (inline CHECK), so find it dynamically
-- rather than assume Postgres's default-naming convention.
DO $$
DECLARE
    con_name TEXT;
BEGIN
    SELECT con.conname INTO con_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'request_log'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%status%';

    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE request_log DROP CONSTRAINT %I', con_name);
    END IF;
END $$;

ALTER TABLE request_log ALTER COLUMN status SET DEFAULT 'pending';

ALTER TABLE request_log ADD CONSTRAINT request_log_status_check
    CHECK (status IN ('pending', 'processing', 'success', 'failed', 'cancelled', 'timed_out', 'stale'));


-- ── New platform-wide table: interaction_log ────────────────────────────────
-- Write-once, append-only. One row per incoming message once intent is
-- classified, regardless of outcome (including small talk / rejected
-- messages). Separate from request_log, which has stricter lookup/update needs.

CREATE TABLE interaction_log (
    interaction_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wechat_openid   VARCHAR(128) NOT NULL,
    group_id        UUID REFERENCES group_config(group_id) ON DELETE SET NULL,
    intent          VARCHAR(30) NOT NULL,
    intent_type     VARCHAR(20) NOT NULL,
    service_type_id UUID REFERENCES service_type(service_type_id) ON DELETE SET NULL,
    request_log_id  UUID REFERENCES request_log(log_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_interaction_log_openid ON interaction_log(wechat_openid);
CREATE INDEX idx_interaction_log_group  ON interaction_log(group_id);
CREATE INDEX idx_interaction_log_created ON interaction_log(created_at DESC);


-- ── U-Choice tables ──────────────────────────────────────────────────────────
-- U-Choice owns its own packing-supply inventory (stretch wrap, tape) — it is
-- NOT a multi-tenant 3PL storing separate customers' goods. The tables below
-- deliberately have NO group_id for exactly this reason.

CREATE TABLE uchoice_sku (
    sku_code    VARCHAR(50) PRIMARY KEY,
    description VARCHAR(200) NOT NULL
);

CREATE TABLE uchoice_storage (
    warehouse_code   VARCHAR(20) NOT NULL,
    sku_code         VARCHAR(50) NOT NULL REFERENCES uchoice_sku(sku_code),
    boxes_per_pallet INTEGER NOT NULL,
    pallet_count     INTEGER NOT NULL DEFAULT 0 CHECK (pallet_count >= 0),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (warehouse_code, sku_code, boxes_per_pallet)
);
-- boxes_per_pallet is a free integer, not a pre-registered catalog value —
-- buckets are created dynamically the first time a given box-count occurs,
-- since real box counts drift from ad-hoc partial picks.

CREATE TABLE uchoice_storage_txn (
    txn_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_code   VARCHAR(20) NOT NULL,
    sku_code         VARCHAR(50) NOT NULL,
    boxes_per_pallet INTEGER NOT NULL,
    pallet_delta     INTEGER NOT NULL,
    txn_type         VARCHAR(20) NOT NULL CHECK (txn_type IN
                        ('inbound', 'outbound', 'convert_in', 'convert_out',
                         'move_in', 'move_out', 'adjust', 'recount')),
    request_log_id   UUID REFERENCES request_log(log_id) ON DELETE SET NULL,
    note             TEXT,
    created_by       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_uchoice_storage_txn_bucket
    ON uchoice_storage_txn(warehouse_code, sku_code, boxes_per_pallet);
CREATE INDEX idx_uchoice_storage_txn_request ON uchoice_storage_txn(request_log_id);
CREATE INDEX idx_uchoice_storage_txn_created ON uchoice_storage_txn(created_at DESC);

CREATE TABLE uchoice_address (
    address_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name   VARCHAR(200) NOT NULL,
    charge_type    VARCHAR(20) NOT NULL CHECK (charge_type IN
                      ('short_delivery', 'delivery', 'truck_transfer')),
    addr           TEXT NOT NULL,
    warehouse_code VARCHAR(20),
    note           TEXT,
    created_by     VARCHAR(128) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE uchoice_storage_fee_ledger (
    ledger_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_code VARCHAR(20) NOT NULL,
    fee_date       DATE NOT NULL,
    pallet_count   INTEGER NOT NULL,
    storage_fee    NUMERIC(10, 2) NOT NULL,
    UNIQUE (warehouse_code, fee_date)
);


-- ── Roles ─────────────────────────────────────────────────────────────────────
-- admin/customer already exist (V2). Add the two U-Choice-specific roles.

INSERT INTO role (name, description) VALUES
    ('warehouseman', 'Confirms inbound/outbound completions, corrects storage (adjust/recount/move)'),
    ('accountant',   'Read-only financial visibility — storage and invoice viewing');


-- ── uchoice_sku seed (8 real SKUs) ──────────────────────────────────────────

INSERT INTO uchoice_sku (sku_code, description) VALUES
    ('s1', 'S1 22 lb Stretch Wrap'),
    ('s2', 'S2 1500 ft Stretch Wrap'),
    ('s3', 'S3 Black Stretch Wrap'),
    ('s4', 'S4 1000 ft Stretch Wrap'),
    ('t1', 'T1 3-inch Clear Packing Tape'),
    ('t2', 'T2 3-inch Dark Brown Packing Tape'),
    ('t3', 'T3 3-inch Light Brown Packing Tape'),
    ('t4', 'T4 2-inch Clear Packing Tape');


-- ── uchoice_address seed (inter-warehouse transfer addresses) ──────────────

INSERT INTO uchoice_address (company_name, charge_type, addr, warehouse_code, note, created_by) VALUES
    ('U-Choice DE Warehouse',  'truck_transfer', '201 Gabor DR, Newark, DE 19711',    'JFK', 'DE warehouse',  'system'),
    ('U-Choice JFK Warehouse', 'truck_transfer', '14502 156th St, Jamaica, NY 11434', 'DE',  'JFK warehouse', 'system');


-- ── Service Types ────────────────────────────────────────────────────────────

INSERT INTO service_type (service_type_id, name, description, input_schema, group_config_schema, confirmation_note, requires_confirmation, targets_existing_request) VALUES

(
    'c1000000-0000-0000-0000-000000000001',
    'uchoice_inbound_request',
    'Customer requests to bring packing-supply inventory into a U-Choice warehouse',
    '{
        "required": ["warehouse_code", "sku_lines"],
        "optional": ["needs_unpacking"],
        "field_hints": {
            "warehouse_code": "JFK or DE",
            "sku_lines": "Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}.",
            "needs_unpacking": "Boolean — true if this inbound shipment needs to be unpacked/unpalletized by warehouse staff on arrival ($300 flat fee). Always ask and always show in the confirmation, even if false."
        }
    }',
    '{}',
    'No storage balance changes yet — this only records the request. Storage updates once the warehouse confirms physical receipt.',
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000002',
    'uchoice_outbound_request',
    'Customer requests to ship packing-supply inventory out of a U-Choice warehouse',
    '{
        "required": ["warehouse_code", "sku_lines", "destination_address_id"],
        "optional": ["new_pallet_count"],
        "field_hints": {
            "warehouse_code": "JFK or DE",
            "sku_lines": "Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}. For a palletized line missing boxes_per_pallet, match against the injected current storage buckets for that SKU+warehouse and propose the largest available bucket as a default — always show this default explicitly in the confirmation so the customer can correct it.",
            "destination_address_id": "Resolve by fuzzy-matching the customer''s description against the injected address candidate list.",
            "new_pallet_count": "$15/pallet — customer wants loose boxes consolidated onto a fresh pallet before shipping. Always ask and always show in the confirmation, even if 0/absent."
        }
    }',
    '{}',
    'No storage balance changes yet — this only records the request. Storage updates once the warehouse confirms physical fulfillment.',
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000003',
    'confirm_inbound_completion',
    'Warehouseman confirms physical receipt of a pending inbound request, updating storage balances',
    '{
        "required": [],
        "optional": ["reference_serial", "received_lines"],
        "field_hints": {
            "reference_serial": "The pending inbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending inbound requests. 0 candidates: tell the user nothing is pending. 1: proceed. Multiple: list them and ask which one.",
            "received_lines": "What was physically received. Defaults to the original request''s sku_lines for palletized lines if unstated. Loose-type lines always require explicit restatement — there is no sensible default for what a warehouseman physically received."
        }
    }',
    '{}',
    'Reported quantities differing from the original request are recorded as ground truth, not blocked — the warehouseman is reporting physical reality.',
    TRUE, TRUE
),

(
    'c1000000-0000-0000-0000-000000000004',
    'confirm_outbound_completion',
    'Warehouseman confirms physical fulfillment of a pending outbound request, updating storage balances',
    '{
        "required": [],
        "optional": ["reference_serial", "fulfillment_lines"],
        "field_hints": {
            "reference_serial": "The pending outbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending outbound requests, same 0/1/N handling as inbound completion.",
            "fulfillment_lines": "What was physically shipped. Palletized lines can default to \"shipped as requested\". Loose-type lines require explicit source_boxes_per_pallet and resulting_boxes_per_pallet — never defaulted (e.g. picked 1 box off an 80-box pallet, 77 remain: convert_out(sku,80,-1) + convert_in(sku,77,+1))."
        }
    }',
    '{}',
    'Arithmetic mismatches between source/resulting counts and the requested box_count are noted, not blocking.',
    TRUE, TRUE
),

(
    'c1000000-0000-0000-0000-000000000005',
    'view_storage',
    'View current storage balances, optionally filtered by warehouse and/or SKU',
    '{
        "required": [],
        "optional": ["warehouse_code", "sku_code"],
        "field_hints": {
            "warehouse_code": "Omit for both warehouses.",
            "sku_code": "Omit for all SKUs."
        }
    }',
    '{}',
    NULL,
    FALSE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000006',
    'view_storage_history',
    'View storage transaction history for a warehouse in a given month',
    '{
        "required": ["warehouse_code", "target_month"],
        "optional": [],
        "field_hints": {
            "warehouse_code": "JFK or DE",
            "target_month": "e.g. 2026-08 — month granularity, not a free date range."
        }
    }',
    '{}',
    NULL,
    FALSE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000007',
    'adjust_storage',
    'Warehouseman records a standalone storage correction (damage, loss, spot-check discrepancy)',
    '{
        "required": ["warehouse_code", "adjustment_lines"],
        "optional": [],
        "field_hints": {
            "adjustment_lines": "Array of {sku_code, boxes_per_pallet, pallet_delta, reason} — plural, so one correction session can report several adjustments in one message."
        }
    }',
    '{}',
    'For a full inventory snapshot use recount_storage instead. For internal repackaging use move_storage instead.',
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000008',
    'recount_storage',
    'Warehouseman reports a full physical inventory snapshot for a warehouse; system computes and applies the diff',
    '{
        "required": ["warehouse_code", "inventory_lines"],
        "optional": [],
        "field_hints": {
            "inventory_lines": "Full snapshot, not a delta — array of {sku_code, boxes_per_pallet, pallet_count}. Any existing bucket omitted from this snapshot is treated as now zero, not unchanged."
        }
    }',
    '{}',
    'The confirmation shows the computed diff against current balances, not the raw snapshot you entered — check it carefully before confirming.',
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-000000000009',
    'move_storage',
    'Warehouseman moves boxes between pallet-count buckets within the same warehouse (internal repackaging, net-zero boxes)',
    '{
        "required": ["warehouse_code", "move_lines"],
        "optional": [],
        "field_hints": {
            "move_lines": "Array of {sku_code, source_boxes_per_pallet, box_count_moved, target_boxes_per_pallet}. Nothing enters or leaves the warehouse — the source bucket loses box_count_moved boxes, the target bucket gains them."
        }
    }',
    '{}',
    NULL,
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-00000000000a',
    'upsert_address',
    'Create or update an entry in the shared U-Choice address book',
    '{
        "required": ["company_name", "charge_type", "addr"],
        "optional": ["note", "warehouse_code"],
        "field_hints": {
            "charge_type": "One of short_delivery, delivery, truck_transfer.",
            "warehouse_code": "Set only if this address is tied to a specific origin warehouse (e.g. an inter-warehouse transfer address).",
            "note": "Free text, e.g. a nickname for the address."
        }
    }',
    '{}',
    'Match against the existing address list is attempted first — if a likely match is found this updates it, otherwise a new address is created. The confirmation states which mode applies; check it before confirming.',
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-00000000000b',
    'role_change',
    'Admin changes another member''s role (and warehouse assignment, if applicable) within the group',
    '{
        "required": ["target_openid", "new_role"],
        "optional": ["warehouse_code"],
        "field_hints": {
            "target_openid": "Resolve via the injected member-list candidate list (wechat_openid + display_name + current role) against a casual name reference.",
            "new_role": "One of admin, customer, warehouseman, accountant.",
            "warehouse_code": "Required only if new_role is warehouseman."
        }
    }',
    '{}',
    NULL,
    TRUE, FALSE
),

(
    'c1000000-0000-0000-0000-00000000000c',
    'view_invoice',
    'View U-Choice''s aggregate warehouse operating cost report for a given month (not a per-customer bill)',
    '{
        "required": ["warehouse_code", "target_month"],
        "optional": [],
        "field_hints": {
            "warehouse_code": "JFK or DE",
            "target_month": "e.g. 2026-08"
        }
    }',
    '{}',
    NULL,
    FALSE, FALSE
);


-- ── Workflows ─────────────────────────────────────────────────────────────────

INSERT INTO workflow (workflow_id, name, description) VALUES

('c2000000-0000-0000-0000-000000000001', 'uchoice_inbound_request',
    'Record an inbound request, reply — no storage change yet'),

('c2000000-0000-0000-0000-000000000002', 'uchoice_outbound_request',
    'Record an outbound request, reply — no storage change yet'),

('c2000000-0000-0000-0000-000000000003', 'confirm_inbound_completion',
    'Validate target request, apply inbound storage txn, stub receiving PDF, complete the original request, reply + cross-group push'),

('c2000000-0000-0000-0000-000000000004', 'confirm_outbound_completion',
    'Validate target request, apply outbound storage txn, stub delivery PDF, complete the original request, reply + cross-group push'),

('c2000000-0000-0000-0000-000000000005', 'view_storage',
    'Query current storage balances, reply immediately'),

('c2000000-0000-0000-0000-000000000006', 'view_storage_history',
    'Query storage transaction history for a month, reply immediately'),

('c2000000-0000-0000-0000-000000000007', 'adjust_storage',
    'Apply standalone storage adjustments, reply'),

('c2000000-0000-0000-0000-000000000008', 'recount_storage',
    'Diff a full inventory snapshot against current balances and apply, reply'),

('c2000000-0000-0000-0000-000000000009', 'move_storage',
    'Apply internal repackaging moves, reply'),

('c2000000-0000-0000-0000-00000000000a', 'upsert_address',
    'Create or update a U-Choice address, reply'),

('c2000000-0000-0000-0000-00000000000b', 'role_change',
    'Apply a member role change, reply'),

('c2000000-0000-0000-0000-00000000000c', 'view_invoice',
    'Compute and reply with the monthly warehouse invoice, reply immediately');


-- ── Workflow Steps ────────────────────────────────────────────────────────────

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES

-- uchoice_inbound_request (2 steps)
('c2000000-0000-0000-0000-000000000001', 1, 'record_uchoice_request', '{}'),
('c2000000-0000-0000-0000-000000000001', 2, 'reply_wechat',           '{}'),

-- uchoice_outbound_request (2 steps)
('c2000000-0000-0000-0000-000000000002', 1, 'record_uchoice_request', '{}'),
('c2000000-0000-0000-0000-000000000002', 2, 'reply_wechat',           '{}'),

-- confirm_inbound_completion (5 steps)
('c2000000-0000-0000-0000-000000000003', 1, 'lookup_and_validate_completion', '{"direction": "inbound"}'),
('c2000000-0000-0000-0000-000000000003', 2, 'apply_inbound_storage_txn',      '{}'),
('c2000000-0000-0000-0000-000000000003', 3, 'generate_pdf_stub',              '{"doc_type": "receiving_confirmation"}'),
('c2000000-0000-0000-0000-000000000003', 4, 'complete_existing_request',      '{}'),
('c2000000-0000-0000-0000-000000000003', 5, 'reply_wechat',                   '{}'),

-- confirm_outbound_completion (5 steps)
('c2000000-0000-0000-0000-000000000004', 1, 'lookup_and_validate_completion', '{"direction": "outbound"}'),
('c2000000-0000-0000-0000-000000000004', 2, 'apply_outbound_storage_txn',     '{}'),
('c2000000-0000-0000-0000-000000000004', 3, 'generate_pdf_stub',              '{"doc_type": "delivery_confirmation"}'),
('c2000000-0000-0000-0000-000000000004', 4, 'complete_existing_request',      '{}'),
('c2000000-0000-0000-0000-000000000004', 5, 'reply_wechat',                   '{}'),

-- view_storage (2 steps)
('c2000000-0000-0000-0000-000000000005', 1, 'query_storage', '{}'),
('c2000000-0000-0000-0000-000000000005', 2, 'reply_wechat',  '{}'),

-- view_storage_history (2 steps)
('c2000000-0000-0000-0000-000000000006', 1, 'query_storage_history', '{}'),
('c2000000-0000-0000-0000-000000000006', 2, 'reply_wechat',          '{}'),

-- adjust_storage (2 steps)
('c2000000-0000-0000-0000-000000000007', 1, 'adjust_storage_txn', '{}'),
('c2000000-0000-0000-0000-000000000007', 2, 'reply_wechat',       '{}'),

-- recount_storage (2 steps)
('c2000000-0000-0000-0000-000000000008', 1, 'recount_storage_txn', '{}'),
('c2000000-0000-0000-0000-000000000008', 2, 'reply_wechat',        '{}'),

-- move_storage (2 steps)
('c2000000-0000-0000-0000-000000000009', 1, 'move_storage_txn', '{}'),
('c2000000-0000-0000-0000-000000000009', 2, 'reply_wechat',     '{}'),

-- upsert_address (2 steps)
('c2000000-0000-0000-0000-00000000000a', 1, 'upsert_address', '{}'),
('c2000000-0000-0000-0000-00000000000a', 2, 'reply_wechat',   '{}'),

-- role_change (2 steps)
('c2000000-0000-0000-0000-00000000000b', 1, 'apply_role_change', '{}'),
('c2000000-0000-0000-0000-00000000000b', 2, 'reply_wechat',      '{}'),

-- view_invoice (2 steps)
('c2000000-0000-0000-0000-00000000000c', 1, 'compute_invoice_handler', '{}'),
('c2000000-0000-0000-0000-00000000000c', 2, 'reply_wechat',            '{}');


-- ── Handler Registry Reference ────────────────────────────────────────────────
-- step_type                       → handler class
-- ────────────────────────────────────────────────
-- record_uchoice_request          → RecordUchoiceRequestHandler
-- lookup_and_validate_completion  → LookupAndValidateCompletionHandler
-- apply_inbound_storage_txn       → ApplyInboundStorageHandler
-- apply_outbound_storage_txn      → ApplyOutboundStorageHandler
-- generate_pdf_stub               → GeneratePdfStubHandler
-- complete_existing_request       → CompleteExistingRequestHandler
-- query_storage                   → QueryStorageHandler
-- query_storage_history           → QueryStorageHistoryHandler
-- adjust_storage_txn              → AdjustStorageHandler
-- recount_storage_txn             → RecountStorageHandler
-- move_storage_txn                → MoveStorageHandler
-- upsert_address                  → UpsertAddressHandler
-- apply_role_change               → RoleChangeHandler
-- compute_invoice_handler         → ComputeInvoiceHandler
-- reply_wechat                    → ReplyWeChatHandler (existing, reused)
-- ============================================================
