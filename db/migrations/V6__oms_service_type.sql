\encoding UTF8
-- ============================================================
-- V6: OMS Work Order integration
-- Logistics WeChat Bot Platform
-- Date: 2026-05-01
--
-- Both FedEx service types now create an OMS work order after label creation.
--
-- fedex_label (no OMS order):
--   Steps: create_fedex_label → oms_create_workorder → reply_wechat
--   Work order has no associatedTrackingNo; thirdNo = serial_number
--
-- fedex_oms_label (with OMS outbound order no.):
--   Steps: create_fedex_label → oms_create_workorder → reply_wechat  (same)
--   Queries OMS for whCode; work order linked via associatedTrackingNoType=2
--
-- Changes:
--   1. New service type: fedex_oms_label
--   2. New workflow: fedex_workorder (shared by both service types)
--   3. Test group: update fedex_label service to use fedex_workorder + add OMS creds
--   4. Test group: add fedex_oms_label service
-- ============================================================


-- ── 1. Service type: fedex_oms_label ─────────────────────────────────────────

INSERT INTO service_type (
    service_type_id, name, description,
    input_schema, group_config_schema, confirmation_note
) VALUES (
    'a1b2c3d4-0003-0000-0000-000000000003',
    'fedex_oms_label',
    'FedEx label creation via YiDiDa, linked to an OMS outbound order',
    '{
        "required": [
            "oms_outbound_order_no",
            "shipper_name", "shipper_phone",
            "shipper_street", "shipper_city", "shipper_state", "shipper_zip",
            "recipient_name", "recipient_phone",
            "recipient_street", "recipient_city", "recipient_state", "recipient_zip",
            "weight_lbs"
        ],
        "optional": [
            "service_level",
            "shipper_corp_name", "shipper_country",
            "recipient_corp_name", "recipient_country",
            "length_in", "width_in", "height_in", "reference_number"
        ],
        "field_hints": {
            "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV)",
            "service_level":        "e.g. PRIORITY_OVERNIGHT, FEDEX_GROUND (default)",
            "weight_lbs":           "numeric value in pounds",
            "shipper_country":      "default is US",
            "recipient_country":    "default is US"
        }
    }',
    '{
        "required": [
            "ydd_api_key", "ydd_cust_id", "ydd_channel_id",
            "oms_app_key", "oms_app_secret", "oms_wh_code"
        ],
        "optional": ["ydd_account_code"],
        "field_hints": {
            "ydd_api_key":      "YiDiDa API key",
            "ydd_cust_id":      "YiDiDa customer ID",
            "ydd_channel_id":   "YiDiDa channel ID",
            "oms_app_key":      "OMS App_Key from xlwms portal",
            "oms_app_secret":   "OMS App_Secret from xlwms portal",
            "oms_wh_code":      "OMS warehouse code fallback (e.g. DE19713)"
        }
    }',
    'Label will be generated and linked to your OMS outbound order. Contact admin immediately if changes are needed.'
)
ON CONFLICT (service_type_id) DO UPDATE SET
    name               = EXCLUDED.name,
    description        = EXCLUDED.description,
    input_schema       = EXCLUDED.input_schema,
    group_config_schema = EXCLUDED.group_config_schema,
    confirmation_note  = EXCLUDED.confirmation_note;


-- ── 2. Workflow: fedex_workorder ──────────────────────────────────────────────
-- Shared by both fedex_label and fedex_oms_label.
-- oms_create_workorder handler auto-detects whether oms_outbound_order_no
-- is present and adjusts behaviour accordingly.

INSERT INTO workflow (workflow_id, name, description) VALUES (
    'af000001-0000-0000-0000-000000000005',
    'fedex_workorder',
    'Create FedEx label via YiDiDa, create OMS work order (linked if OMS order no. provided), reply to WeChat'
)
ON CONFLICT (workflow_id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description;

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
('af000001-0000-0000-0000-000000000005', 1, 'create_fedex_label',   '{"carrier": "fedex"}'),
('af000001-0000-0000-0000-000000000005', 2, 'oms_create_workorder', '{}'),
('af000001-0000-0000-0000-000000000005', 3, 'reply_wechat',         '{}')
ON CONFLICT (workflow_id, step_order) DO UPDATE SET
    step_type = EXCLUDED.step_type,
    config    = EXCLUDED.config;


-- ── 3. Test group: update fedex_label service ─────────────────────────────────
-- Switch from fedex_only (no OMS) to fedex_workorder, add OMS credentials.
-- Group:   a81d11df-b487-410b-abdf-5126f13e4992
-- Service: a1b2c3d4-0001-0000-0000-000000000001 (fedex_label)

UPDATE group_service
SET
    workflow_id = 'af000001-0000-0000-0000-000000000005',
    config      = config || '{
        "oms_app_key":    "7067eec5f4ce4b3fa4321aabbe2623ab",
        "oms_app_secret": "0b6069240b1d49438761c3155a36ddfc",
        "oms_wh_code":    "DE19713"
    }'::jsonb
WHERE group_id        = 'a81d11df-b487-410b-abdf-5126f13e4992'
  AND service_type_id = 'a1b2c3d4-0001-0000-0000-000000000001';


-- ── 4. Test group: add fedex_oms_label service ────────────────────────────────

INSERT INTO group_service (group_id, service_type_id, workflow_id, config) VALUES (
    'a81d11df-b487-410b-abdf-5126f13e4992',
    'a1b2c3d4-0003-0000-0000-000000000003',
    'af000001-0000-0000-0000-000000000005',
    '{
        "ydd_api_key":    "abc12345",
        "ydd_cust_id":    "F000179",
        "ydd_channel_id": "Fedex home delivery 洛杉矶渠道",
        "oms_app_key":    "7067eec5f4ce4b3fa4321aabbe2623ab",
        "oms_app_secret": "0b6069240b1d49438761c3155a36ddfc",
        "oms_wh_code":    "DE19713"
    }'
)
ON CONFLICT (group_id, service_type_id) DO UPDATE SET
    workflow_id = EXCLUDED.workflow_id,
    config      = EXCLUDED.config;


-- ── Handler registry reference (V6) ──────────────────────────────────────────
-- step_type              → handler class
-- ─────────────────────────────────────────────────────────
-- oms_create_workorder   → OMSCreateWorkorderHandler
-- ============================================================
