\encoding UTF8
-- ============================================================
-- V12: Real-world context updates
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- Driven by a review of 57 real outbound dispatch messages (Outbound_Sample.xlsx):
--   1. warehouse_code is never stated in real messages — default to JFK
--      instead of always asking, shown in the confirmation for correction.
--   2. Several real addresses have no company name at all, only a location
--      nickname or a bare address ("新fast track", "旧fast track") — make
--      company_name optional on both the upsert_address service and the
--      uchoice_address table itself.
-- ============================================================

-- ── uchoice_outbound_request: warehouse_code becomes optional, defaults to JFK ──

UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{required}',
        '["sku_lines", "destination_address_id"]'
    ),
    '{optional}',
    '["new_pallet_count", "warehouse_code"]'
)
WHERE name = 'uchoice_outbound_request';

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,warehouse_code}',
    '"JFK or DE. Real customers almost never state this explicitly — if not mentioned, do NOT ask; leave it unset and the system will default to JFK, shown in the confirmation for the customer to correct."'
)
WHERE name = 'uchoice_outbound_request';

-- ── upsert_address / uchoice_address: company_name becomes optional ────────

ALTER TABLE uchoice_address ALTER COLUMN company_name DROP NOT NULL;

UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{required}',
        '["charge_type", "addr", "warehouse_code"]'
    ),
    '{optional}',
    '["note", "company_name"]'
)
WHERE name = 'upsert_address';

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,company_name}',
    '"Optional — some real addresses are only ever referred to by a bare address or a location nickname, with no formal company name ever given. Leave unset rather than guessing one."'
)
WHERE name = 'upsert_address';
