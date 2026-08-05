-- ── self_pickup charge type ─────────────────────────────────────────────────
-- Fourth charge_type tier: $0/pallet, for stock that never physically leaves
-- the building — either the warehouse operator (Transworld) converts
-- U-Choice-tracked inventory into its own stock kept in place ("JFK to JFK" /
-- "DE to DE"), or a walk-in customer picks product up in person ("散客").
-- Both behave exactly like an ordinary outbound to an external party
-- (origin storage decrements, nothing increments anywhere) — no special
-- transfer handling, since destination_warehouse_code is deliberately left
-- NULL on all three new rows below.

ALTER TABLE uchoice_address DROP CONSTRAINT uchoice_address_charge_type_check;
ALTER TABLE uchoice_address ADD CONSTRAINT uchoice_address_charge_type_check CHECK (charge_type IN
    ('short_delivery', 'delivery', 'truck_transfer', 'self_pickup'));

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,charge_type}',
    '"One of short_delivery, delivery, truck_transfer, self_pickup."'
)
WHERE name = 'upsert_address';

INSERT INTO uchoice_address (company_name, charge_type, addr, warehouse_code, note, created_by) VALUES
    ('JFK仓库自提留存', 'self_pickup', '14502 156th St, Jamaica, NY 11434', 'JFK', '仓库自留，货物不离开JFK仓', 'system'),
    ('DE仓库自提留存',  'self_pickup', '201 Gabor DR, Newark, DE 19711',    'DE',  '仓库自留，货物不离开DE仓',  'system'),
    ('散客',           'self_pickup', '',                                   NULL,  '客户本人到仓库自行取货，不限仓库', 'system');
