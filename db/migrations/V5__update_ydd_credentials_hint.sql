\encoding UTF8
-- ============================================================
-- V5: Update group_config_schema field hints for YiDiDa
-- Logistics WeChat Bot Platform
-- Date: 2026-04-29
--
-- Clarifies what each credential field actually maps to in YiDiDa API:
--   ydd_cust_id   → YiDiDa login username
--   ydd_api_key   → YiDiDa login password
--   ydd_channel_id → shouHuoQuDao (shipping channel name)
--
-- Also updates test group's group_service.config with real credentials.
-- ============================================================

-- Update field hints so admins know what to provide
UPDATE service_type
SET group_config_schema = '{
    "required": [
        "ydd_cust_id",
        "ydd_api_key",
        "ydd_channel_id"
    ],
    "optional": [
        "ydd_account_code",
        "oms_api_key"
    ],
    "field_hints": {
        "ydd_cust_id":      "YiDiDa login username (e.g. F000179)",
        "ydd_api_key":      "YiDiDa login password",
        "ydd_channel_id":   "Shipping channel name (shouHuoQuDao), e.g. Fedex Home Delivery 洛杉矶渠道",
        "ydd_account_code": "Optional billing account code",
        "oms_api_key":      "OMS API key (required if workflow includes oms_record step)"
    }
}'::jsonb
WHERE name IN ('fedex_label', 'ups_label');


-- Update test group service config with real YiDiDa credentials
UPDATE group_service
SET config = '{
    "ydd_cust_id":    "F000179",
    "ydd_api_key":    "abc12345",
    "ydd_channel_id": "Fedex home delivery 洛杉矶渠道"
}'::jsonb
WHERE group_id = (
    SELECT group_id FROM group_config
    WHERE wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
)
AND service_type_id = 'a1b2c3d4-0001-0000-0000-000000000001';
