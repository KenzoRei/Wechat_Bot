-- ============================================================
-- V3: Update input_schema for fedex_label and ups_label
-- Logistics WeChat Bot Platform
-- Date: 2026-04-29
--
-- Changes from V2:
--   - Added shipper fields (shipper_name, shipper_phone, shipper_street, etc.)
--   - Renamed recipient address fields to use recipient_ prefix
--   - Moved service_level from required → optional (defaults to GROUND in handler)
--   - Added optional: shipper_corp_name, recipient_corp_name, reference_number
--   - Removed package_type (not used by YiDiDa handler)
-- ============================================================

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
        "recipient_corp_name",
        "length_in",
        "width_in",
        "height_in",
        "reference_number"
    ],
    "field_hints": {
        "service_level":    "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND",
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
        "recipient_corp_name",
        "length_in",
        "width_in",
        "height_in",
        "reference_number"
    ],
    "field_hints": {
        "service_level":    "e.g. UPS_GROUND, UPS_2ND_DAY_AIR, UPS_NEXT_DAY_AIR, default is UPS_GROUND",
        "weight_lbs":       "numeric value in pounds",
        "reference_number": "Optional reference field printed on label (e.g. order number, customer name)"
    }
}'::jsonb
WHERE name = 'ups_label';
