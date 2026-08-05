\encoding UTF8
-- ============================================================
-- V7: upsert_address — warehouse_code required for every address
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- warehouse_code was optional, meaningful only for the two seeded
-- inter-warehouse transfer addresses. Now required for every address per
-- explicit decision — every address must state which warehouse it's
-- associated with, not just truck_transfer ones.
-- ============================================================

UPDATE service_type
SET input_schema = '{
    "required": ["company_name", "charge_type", "addr", "warehouse_code"],
    "optional": ["note"],
    "field_hints": {
        "note": "Free text, e.g. a nickname for the address.",
        "charge_type": "One of short_delivery, delivery, truck_transfer.",
        "warehouse_code": "JFK or DE — which warehouse this address is associated with. Required for every address, not just truck_transfer ones."
    }
}'
WHERE name = 'upsert_address';
