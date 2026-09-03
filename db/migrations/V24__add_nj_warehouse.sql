\encoding UTF8
-- ============================================================
-- V24: add NJ (120 Raskulinecz Rd, Carteret, NJ 07008) as a third warehouse
-- Logistics WeChat Bot Platform
-- Date: 2026-09-03
--
-- Seeds the internal-transfer address book so NJ is a selectable outbound
-- destination from JFK and DE, and JFK/DE are selectable destinations from
-- NJ, mirroring the existing JFK<->DE pair (V2__seed_catalog.sql), plus an
-- NJ self-pickup entry. Uses explicit column lists and stable, deterministic
-- address_ids so this migration has a real ON CONFLICT target and doesn't
-- depend on the table's column order. customer_id is left NULL for these
-- warehouse-owned rows, matching every existing warehouse-transfer/
-- self-pickup address -- they're operational addresses, not tied to a
-- customer directory entry.
--
-- core.uchoice_constants.VALID_WAREHOUSE_CODES is updated in application
-- code (this migration and that code deploy together, same coordinated-
-- cutover rollout as V22).
--
-- Idempotent for both an existing deployment and a fresh database.
-- ============================================================

INSERT INTO uchoice_address (
    address_id, company_name, charge_type, addr, warehouse_code, note,
    created_by, destination_warehouse_code
) VALUES
    ('a1000000-0024-0000-0000-000000000001', 'NJ Warehouse', 'truck_transfer',
     '120 Raskulinecz Rd, Carteret, NJ 07008', 'JFK', 'NJ warehouse',
     'migration_v24', 'NJ'),
    ('a1000000-0024-0000-0000-000000000002', 'NJ Warehouse', 'truck_transfer',
     '120 Raskulinecz Rd, Carteret, NJ 07008', 'DE', 'NJ warehouse',
     'migration_v24', 'NJ'),
    ('a1000000-0024-0000-0000-000000000003', 'DE Warehouse', 'truck_transfer',
     '201 Gabor DR, Newark, DE 19711', 'NJ', 'DE warehouse',
     'migration_v24', 'DE'),
    ('a1000000-0024-0000-0000-000000000004', 'JFK Warehouse', 'truck_transfer',
     '14502 156th St, Jamaica, NY 11434', 'NJ', 'JFK warehouse',
     'migration_v24', 'JFK'),
    ('a1000000-0024-0000-0000-000000000005', 'NJ仓库自提留存', 'self_pickup',
     '120 Raskulinecz Rd, Carteret, NJ 07008', 'NJ', '仓库自留，货物不离开NJ仓',
     'migration_v24', NULL)
ON CONFLICT (address_id) DO UPDATE
SET company_name = EXCLUDED.company_name,
    charge_type = EXCLUDED.charge_type,
    addr = EXCLUDED.addr,
    warehouse_code = EXCLUDED.warehouse_code,
    note = EXCLUDED.note,
    destination_warehouse_code = EXCLUDED.destination_warehouse_code;

-- Field-hint text: "JFK or DE" -> "JFK, DE, or NJ" (jsonb_set, same
-- technique V9 uses). Only touches services whose field_hints literally
-- name the two old codes; everywhere else in application code already
-- builds this text dynamically from VALID_WAREHOUSE_CODES.
UPDATE service_type
SET input_schema = jsonb_set(input_schema, '{field_hints,warehouse_code}', '"JFK, DE, or NJ"')
WHERE name IN ('uchoice_inbound_request', 'uchoice_outbound_request', 'view_storage_history', 'view_invoice')
  AND input_schema -> 'field_hints' ? 'warehouse_code';

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema, '{field_hints,warehouse_code}',
    '"JFK, DE, or NJ — which warehouse this address is associated with. Required for every address, not just truck_transfer ones."'
)
WHERE name = 'upsert_address'
  AND input_schema -> 'field_hints' ? 'warehouse_code';

UPDATE service_type
SET input_schema = jsonb_set(input_schema, '{field_hints,warehouse_code}', '"Omit for all warehouses."')
WHERE name = 'view_storage'
  AND input_schema -> 'field_hints' ? 'warehouse_code';
