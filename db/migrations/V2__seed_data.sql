\encoding UTF8
-- ============================================================
-- V2: Seed Data — Service Types, Workflows, Workflow Steps
-- Logistics WeChat Bot Platform
-- Date: 2026-04-26
--
-- Rules:
--   1. Never edit this file after deployment — add V3, V4... for changes
--   2. Every step_type here must have a matching handler in the handler registry
--   3. workflow_id values are hardcoded UUIDs so workflow_step can reference them
-- ============================================================


-- ── Service Types ─────────────────────────────────────────────────────────────
-- input_schema tells Claude exactly which fields to collect for each service.
-- "required" fields must all be present before Claude triggers confirmation.

INSERT INTO service_type (service_type_id, name, description, input_schema, group_config_schema, confirmation_note) VALUES

(
    'a1b2c3d4-0001-0000-0000-000000000001',
    'fedex_label',
    'FedEx shipping label creation via YiDiDa',
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
            "service_level": "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND",
            "shipper_country": "default is US",
            "recipient_country": "default is US",
            "weight_lbs":    "numeric value in pounds",
            "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)"
        }
    }',
    '{
        "required": [
            "ydd_api_key",
            "ydd_cust_id",
            "ydd_channel_id"
        ],
        "optional": [
            "ydd_account_code",
            "oms_api_key"
        ],
        "field_hints": {
            "ydd_api_key":      "YiDiDa API key for this customer group",
            "ydd_cust_id":      "YiDiDa customer ID, provided during YiDiDa onboarding",
            "ydd_channel_id":   "YiDiDa channel ID for this shipper account",
            "ydd_account_code": "Optional billing account code",
            "oms_api_key":      "OMS API key for this customer group (required if workflow includes oms_record step)"
        }
    }',
    'Label will be generated automatically. Shipping cost will be charged to the company account. Contact admin immediately if changes are needed.'
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
            "ydd_account_code",
            "oms_api_key"
        ],
        "field_hints": {
            "ydd_api_key":      "YiDiDa API key for this customer group",
            "ydd_cust_id":      "YiDiDa customer ID, provided during YiDiDa onboarding",
            "ydd_channel_id":   "YiDiDa channel ID for this shipper account",
            "ydd_account_code": "Optional billing account code",
            "oms_api_key":      "OMS API key for this customer group (required if workflow includes oms_record step)"
        }
    }',
    'Label will be generated automatically. Shipping cost will be charged to the company account. Contact admin immediately if changes are needed.'
);


-- ── Workflows ─────────────────────────────────────────────────────────────────
-- Each workflow is a named sequence of steps.
-- Groups pick which workflow applies to them in group_service.workflow_id.
-- Note: UUIDs must use only hex characters (0-9, a-f).

INSERT INTO workflow (workflow_id, name, description) VALUES

-- FedEx workflows
('af000001-0000-0000-0000-000000000001', 'fedex_with_oms',
    'Create FedEx label via YiDiDa, record in OMS, reply to WeChat'),

('af000001-0000-0000-0000-000000000002', 'fedex_only',
    'Create FedEx label via YiDiDa, reply to WeChat — no OMS record'),

-- UPS workflows
('af000001-0000-0000-0000-000000000003', 'ups_with_oms',
    'Create UPS label via YiDiDa, record in OMS, reply to WeChat'),

('af000001-0000-0000-0000-000000000004', 'ups_only',
    'Create UPS label via YiDiDa, reply to WeChat — no OMS record');


-- ── Workflow Steps ────────────────────────────────────────────────────────────
-- step_type must exactly match a key in the handler registry.
-- config is passed directly to the handler's handle(context, config) method.
-- reply_wechat is always the last step in every workflow.

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES

-- fedex_with_oms (3 steps)
('af000001-0000-0000-0000-000000000001', 1, 'create_fedex_label',
    '{"carrier": "fedex"}'),
('af000001-0000-0000-0000-000000000001', 2, 'oms_record',
    '{"record_type": "outbound"}'),
('af000001-0000-0000-0000-000000000001', 3, 'reply_wechat',
    '{}'),

-- fedex_only (2 steps)
('af000001-0000-0000-0000-000000000002', 1, 'create_fedex_label',
    '{"carrier": "fedex"}'),
('af000001-0000-0000-0000-000000000002', 2, 'reply_wechat',
    '{}'),

-- ups_with_oms (3 steps)
('af000001-0000-0000-0000-000000000003', 1, 'create_ups_label',
    '{"carrier": "ups"}'),
('af000001-0000-0000-0000-000000000003', 2, 'oms_record',
    '{"record_type": "outbound"}'),
('af000001-0000-0000-0000-000000000003', 3, 'reply_wechat',
    '{}'),

-- ups_only (2 steps)
('af000001-0000-0000-0000-000000000004', 1, 'create_ups_label',
    '{"carrier": "ups"}'),
('af000001-0000-0000-0000-000000000004', 2, 'reply_wechat',
    '{}');


-- ── Handler Registry Reference ────────────────────────────────────────────────
-- Every step_type used above must be registered here in code.
-- If a step_type is added here without a matching handler, the workflow engine crashes.
--
-- step_type             → handler class
-- ─────────────────────────────────────
-- create_fedex_label    → FedExLabelHandler
-- create_ups_label      → UPSLabelHandler
-- oms_record            → OMSRecordHandler
-- reply_wechat          → ReplyWeChatHandler
-- ============================================================
