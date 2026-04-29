\encoding UTF8
-- ============================================================
-- V4: Combined schema update
-- Logistics WeChat Bot Platform
-- Date: 2026-04-29
--
-- Changes:
--   1. Update input_schema for fedex_label and ups_label
--      - Add shipper_* fields (name, phone, street, city, state, zip)
--      - Rename recipient address fields to recipient_* prefix
--      - Move service_level → optional (handler defaults to GROUND)
--      - Add optional: shipper_country, recipient_country (default US)
--      - Add optional: shipper_corp_name, recipient_corp_name, reference_number
--      - Remove package_type (not used by YiDiDa handler)
--
--   2. Add context JSONB column to group_config
--      - Stores group-specific knowledge for the AI
--      - e.g. location presets (LAX, DE warehouse info)
--
--   3. Set context for test group (wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A)
-- ============================================================


-- ── 1. Update input_schema ────────────────────────────────────────────────────

UPDATE service_type
SET input_schema = '{
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
        "service_level":    "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND",
        "shipper_country":  "default is US",
        "recipient_country":"default is US",
        "weight_lbs":       "numeric value in pounds",
        "reference_number": "Optional reference field printed on label (e.g. order number, customer name)"
    }
}'::jsonb
WHERE name = 'fedex_label';


UPDATE service_type
SET input_schema = '{
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
        "service_level":    "e.g. UPS_GROUND, UPS_2ND_DAY_AIR, UPS_NEXT_DAY_AIR, default is UPS_GROUND",
        "shipper_country":  "default is US",
        "recipient_country":"default is US",
        "weight_lbs":       "numeric value in pounds",
        "reference_number": "Optional reference field printed on label (e.g. order number, customer name)"
    }
}'::jsonb
WHERE name = 'ups_label';


-- ── 2. Add context column to group_config ─────────────────────────────────────

ALTER TABLE group_config
    ADD COLUMN IF NOT EXISTS context JSONB;


-- ── 3. Set context for test group ─────────────────────────────────────────────
-- Location presets use neutral field names (name, corp_name, phone, street, etc.)
-- The AI maps them to shipper_* or recipient_* based on message context.

UPDATE group_config
SET context = '{
    "location_presets": {
        "LAX": {
            "corp_name": "TRANS WORLD LAX",
            "name":      "Paul Yang",
            "phone":     "626-242-5505",
            "street":    "293 E REDONDO BEACH BLVD",
            "city":      "GARDENA",
            "state":     "CA",
            "zip":       "90248",
            "country":   "US"
        },
        "DE": {
            "corp_name": "TRANS WORLD DE",
            "name":      "Zorro Zhang",
            "phone":     "347-204-0602",
            "street":    "201 GABOR DR",
            "city":      "NEWARK",
            "state":     "DE",
            "zip":       "19711",
            "country":   "US"
        }
    }
}'::jsonb
WHERE wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A';
