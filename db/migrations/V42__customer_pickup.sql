-- V42: customer pickup (客户自提) -- docs/ai-collaboration/2026-11-customer-pickup/plan.md
--
-- Adds uchoice_address.pickup_kind so code (never label text) can tell the
-- self-pickup rows apart:
--   'retention'  仓库自提留存 -- the recipient is Trans World itself; stock is
--                deducted as usual (unchanged behaviour). One row per warehouse.
--   'customer'   客户自提 -- a named outside customer collects at the warehouse.
--                ONE general row (warehouse_code NULL = valid for any source
--                warehouse; the request's own warehouse_code says where).
--   'legacy'     散客 -- retired, kept so past requests that reference it still
--                render. The resolver never returns it.
-- NULL on every ordinary address.
--
-- Rows are identified by their stable seed IDs, never by editable labels. The
-- migration FAILS CLOSED if any of the four known seed rows is missing.
-- Idempotent for both an existing deployment and a fresh database.

ALTER TABLE uchoice_address ADD COLUMN IF NOT EXISTS pickup_kind VARCHAR(10);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uchoice_address_pickup_kind_check') THEN
        ALTER TABLE uchoice_address ADD CONSTRAINT uchoice_address_pickup_kind_check
            CHECK (pickup_kind IS NULL OR pickup_kind IN ('customer', 'retention', 'legacy'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uchoice_address_pickup_kind_charge_type_check') THEN
        ALTER TABLE uchoice_address ADD CONSTRAINT uchoice_address_pickup_kind_charge_type_check
            CHECK (pickup_kind IS NULL OR charge_type = 'self_pickup');
    END IF;
END $$;

-- Fail closed: all four known seed rows must exist before anything is flagged.
DO $$
DECLARE
    found integer;
BEGIN
    SELECT count(*) INTO found FROM uchoice_address
    WHERE address_id IN (
        'bb8c35cb-dda1-404d-9294-682ed3df5659',   -- JFK仓库自提留存
        'a25bb922-2340-41ac-8e2d-f0a5192d85d0',   -- DE仓库自提留存
        'a1000000-0024-0000-0000-000000000005',   -- NJ仓库自提留存
        '5a1cd32c-f2a3-492d-b2c3-360acfff7514'    -- 散客
    );
    IF found <> 4 THEN
        RAISE EXCEPTION 'V42: expected the 4 known self-pickup seed rows, found % -- refusing to guess', found;
    END IF;
END $$;

UPDATE uchoice_address SET pickup_kind = 'retention'
WHERE address_id IN (
    'bb8c35cb-dda1-404d-9294-682ed3df5659',
    'a25bb922-2340-41ac-8e2d-f0a5192d85d0',
    'a1000000-0024-0000-0000-000000000005'
) AND pickup_kind IS DISTINCT FROM 'retention';

UPDATE uchoice_address SET pickup_kind = 'legacy'
WHERE address_id = '5a1cd32c-f2a3-492d-b2c3-360acfff7514'
  AND pickup_kind IS DISTINCT FROM 'legacy';

-- Cardinality: exactly one general customer row; one retention row per warehouse.
CREATE UNIQUE INDEX IF NOT EXISTS uq_uchoice_address_pickup_customer
    ON uchoice_address (pickup_kind) WHERE pickup_kind = 'customer';
CREATE UNIQUE INDEX IF NOT EXISTS uq_uchoice_address_pickup_retention
    ON uchoice_address (warehouse_code) WHERE pickup_kind = 'retention';

-- The one general 客户自提 row. addr is '' like 散客 (renderers already
-- handle an empty address); warehouse_code NULL = any source warehouse;
-- destination_warehouse_code NULL = a plain outbound with fee $0.
INSERT INTO uchoice_address (
    address_id, company_name, charge_type, addr, warehouse_code, note,
    created_by, destination_warehouse_code, pickup_kind
) VALUES (
    'a1000000-0042-0000-0000-000000000001', '客户自提', 'self_pickup', '', NULL,
    '客户到仓库自行取货，需登记取货人姓名', 'migration_v42', NULL, 'customer'
)
ON CONFLICT (address_id) DO NOTHING;
