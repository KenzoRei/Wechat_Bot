\encoding UTF8
-- ============================================================
-- V22: warehouse assignment becomes a list (warehouse_code -> warehouse_codes)
-- Logistics WeChat Bot Platform
-- Date: 2026-09-03
--
-- Lets one warehouseman/Kefu staff member cover more than one warehouse.
-- Native Postgres array, not a join table -- there is no warehouse entity
-- table in this schema (codes are a hardcoded frozenset in
-- core/uchoice_constants.py), so a join table would add a schema layer
-- with nothing real to join to. See
-- docs/archive/collaboration/2026-09-warehouse-array-and-cancel-service/
-- for the full design discussion.
--
-- Coordinated cutover: this migration and the corresponding app-code
-- deploy (reads warehouse_codes, not warehouse_code) go out together in
-- one window. No dual-read compatibility shim.
-- ============================================================

ALTER TABLE group_member
    ALTER COLUMN warehouse_code TYPE character varying(20)[]
    USING CASE WHEN warehouse_code IS NULL THEN NULL ELSE ARRAY[warehouse_code] END;
ALTER TABLE group_member RENAME COLUMN warehouse_code TO warehouse_codes;

ALTER TABLE kefu_staff
    ALTER COLUMN warehouse_code TYPE character varying(20)[]
    USING CASE WHEN warehouse_code IS NULL THEN NULL ELSE ARRAY[warehouse_code] END;
ALTER TABLE kefu_staff RENAME COLUMN warehouse_code TO warehouse_codes;

-- role_change's own input_schema: warehouse_code (single) -> warehouse_codes
-- (array), same jsonb_set technique V9 uses.
UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{optional}',
        (
            SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
            FROM jsonb_array_elements(input_schema -> 'optional') elem
            WHERE elem <> '"warehouse_code"'::jsonb
        ) || '["warehouse_codes"]'::jsonb
    ),
    '{field_hints,warehouse_codes}',
    '"Required only if new_role is warehouseman. One or more codes."'::jsonb
)
WHERE name = 'role_change';

UPDATE service_type
SET input_schema = input_schema #- '{field_hints,warehouse_code}'
WHERE name = 'role_change';
