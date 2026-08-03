\encoding UTF8
-- ============================================================
-- V2: Seed Catalog — Roles, Service Types, Workflows, Workflow Steps
-- Logistics WeChat Bot Platform
-- Date: 2026-08-03
--
-- Global, group-agnostic catalog only. Group-specific setup (groups,
-- members, credentials, service-role grants) is done live via the Admin
-- API onboarding flow — see docs/ops/admin-api-reference.md.
--
-- fedex_label and fedex_oms_label from the old design are merged here:
-- oms_outbound_order_no is now an OPTIONAL field on a single fedex_label
-- service. OMSCreateWorkorderHandler already branches on its presence —
-- creates a plain work order if absent, a linked one if present.
-- ============================================================


-- ── Roles ─────────────────────────────────────────────────────────────────────

INSERT INTO role (name, description) VALUES
    ('admin',    'Full access to group services and admin-level actions'),
    ('customer', 'Standard requester — access limited to explicitly granted services');


-- ── Service Types ─────────────────────────────────────────────────────────────

INSERT INTO service_type (service_type_id, name, description, input_schema, group_config_schema, confirmation_note) VALUES

(
    'a1b2c3d4-0001-0000-0000-000000000001',
    'fedex_label',
    'FedEx shipping label creation via YiDiDa, with optional OMS outbound order linkage',
    '{
        "required": [
            "shipper_name",
            "shipper_phone",
            "shipper_street",
            "shipper_city",
            "shipper_state",
            "shipper_zip",
            "recipient_name",
            "recipient_phone",
            "recipient_street",
            "recipient_city",
            "recipient_state",
            "recipient_zip",
            "weight_lbs"
        ],
        "optional": [
            "oms_outbound_order_no",
            "service_level",
            "shipper_corp_name",
            "shipper_country",
            "recipient_corp_name",
            "recipient_country",
            "length_in",
            "width_in",
            "height_in",
            "reference_number"
        ],
        "field_hints": {
            "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV) — only collect if the customer volunteers it, never ask proactively. Links the created label to their existing OMS order.",
            "service_level":    "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND",
            "shipper_country":  "default is US",
            "recipient_country":"default is US",
            "weight_lbs":       "numeric value in pounds",
            "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)"
        }
    }',
    '{
        "required": [
            "ydd_api_key",
            "ydd_cust_id",
            "ydd_channel_id",
            "oms_app_key",
            "oms_app_secret",
            "oms_wh_code"
        ],
        "optional": [
            "ydd_account_code"
        ],
        "field_hints": {
            "ydd_api_key":      "YiDiDa API key for this customer group",
            "ydd_cust_id":      "YiDiDa customer ID, provided during YiDiDa onboarding",
            "ydd_channel_id":   "YiDiDa channel ID for this shipper account",
            "ydd_account_code": "Optional billing account code",
            "oms_app_key":      "OMS App_Key from xlwms admin portal",
            "oms_app_secret":   "OMS App_Secret from xlwms admin portal",
            "oms_wh_code":      "OMS warehouse code fallback (e.g. DE19713), used if the outbound order query returns none"
        }
    }',
    'Label will be generated automatically and an OMS work order created. Shipping cost will be charged to the company account. Contact admin immediately if changes are needed.'
),

(
    'a1b2c3d4-0002-0000-0000-000000000002',
    'ups_label',
    'UPS shipping label creation via YiDiDa',
    '{
        "required": [
            "shipper_name",
            "shipper_phone",
            "shipper_street",
            "shipper_city",
            "shipper_state",
            "shipper_zip",
            "recipient_name",
            "recipient_phone",
            "recipient_street",
            "recipient_city",
            "recipient_state",
            "recipient_zip",
            "weight_lbs"
        ],
        "optional": [
            "service_level",
            "shipper_corp_name",
            "shipper_country",
            "recipient_corp_name",
            "recipient_country",
            "length_in",
            "width_in",
            "height_in",
            "reference_number"
        ],
        "field_hints": {
            "service_level": "e.g. UPS_GROUND, UPS_2ND_DAY_AIR, UPS_NEXT_DAY_AIR, default is UPS_GROUND",
            "weight_lbs":    "numeric value in pounds",
            "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)",
            "shipper_country": "default is US",
            "recipient_country": "default is US"
        }
    }',
    '{
        "required": [
            "ydd_api_key",
            "ydd_cust_id",
            "ydd_channel_id"
        ],
        "optional": [
            "ydd_account_code"
        ],
        "field_hints": {
            "ydd_api_key":      "YiDiDa API key for this customer group",
            "ydd_cust_id":      "YiDiDa customer ID, provided during YiDiDa onboarding",
            "ydd_channel_id":   "YiDiDa channel ID for this shipper account",
            "ydd_account_code": "Optional billing account code"
        }
    }',
    'Label will be generated automatically. Shipping cost will be charged to the company account. Contact admin immediately if changes are needed.'
);


-- ── Workflows ─────────────────────────────────────────────────────────────────

INSERT INTO workflow (workflow_id, name, description) VALUES

('af000001-0000-0000-0000-000000000005', 'fedex_workorder',
    'Create FedEx label via YiDiDa, create OMS work order (linked if OMS order no. provided), reply to WeChat'),

('af000001-0000-0000-0000-000000000004', 'ups_only',
    'Create UPS label via YiDiDa, reply to WeChat — no OMS work order');


-- ── Workflow Steps ────────────────────────────────────────────────────────────

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES

-- fedex_workorder (3 steps)
('af000001-0000-0000-0000-000000000005', 1, 'create_fedex_label',   '{"carrier": "fedex"}'),
('af000001-0000-0000-0000-000000000005', 2, 'oms_create_workorder', '{}'),
('af000001-0000-0000-0000-000000000005', 3, 'reply_wechat',         '{}'),

-- ups_only (2 steps)
('af000001-0000-0000-0000-000000000004', 1, 'create_ups_label', '{"carrier": "ups"}'),
('af000001-0000-0000-0000-000000000004', 2, 'reply_wechat',     '{}');


-- ── Handler Registry Reference ────────────────────────────────────────────────
-- step_type             → handler class
-- ─────────────────────────────────────
-- create_fedex_label    → FedExLabelHandler
-- create_ups_label      → UPSLabelHandler
-- oms_create_workorder  → OMSCreateWorkorderHandler
-- reply_wechat          → ReplyWeChatHandler
-- ============================================================
