\encoding UTF8
-- ============================================================
-- V44: add_address becomes create-only -- catalog wording
-- Logistics WeChat Bot Platform
--
-- Step 2 of the add_address / update_address split (v1.12.0), after V43's
-- pure rename. add_address no longer edits existing addresses (that is the
-- separate update_address service), so its description, keywords and
-- confirmation_note must stop promising "update" / "maintain" -- the AI
-- routes partly on these. Behaviour changes ship in the same release as the
-- code (AddAddressHandler is create-only, duplicate guard); apply V43 + V44
-- only inside the paused cutover (CHANGELOG v1.12.0).
-- Idempotent. Contains no COMMIT.
-- ============================================================

UPDATE service_type
SET description = '新增一条收货地址到共享地址簿，含公司名称、详细地址及对应计费类型。仅用于新增；修改已有地址请使用「更新地址」。',
    confirmation_note = '此操作将新增一条地址记录。若相同地址（同仓库、同计费类型）已存在，系统会提示无需重复新增。',
    keywords = '["新增地址", "添加地址", "地址簿"]'::jsonb
WHERE name = 'add_address';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM service_type WHERE name = 'add_address') THEN
        RAISE EXCEPTION 'V44: add_address service_type not found (apply V43 first)';
    END IF;
END $$;
