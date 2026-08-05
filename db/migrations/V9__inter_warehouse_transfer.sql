-- ── Inter-warehouse transfer support ────────────────────────────────────────
-- When a customer's outbound request is addressed to one of our own two
-- warehouses (not an external customer), confirming it must update BOTH
-- sides' storage in one step: origin decreases, destination increases. This
-- column marks which two rows in uchoice_address represent that case and
-- names the actual destination warehouse, so the completion handler doesn't
-- have to infer it from company_name text.

ALTER TABLE uchoice_address ADD COLUMN destination_warehouse_code VARCHAR(20);

UPDATE uchoice_address SET destination_warehouse_code = 'DE'  WHERE company_name = 'U-Choice DE Warehouse';
UPDATE uchoice_address SET destination_warehouse_code = 'JFK' WHERE company_name = 'U-Choice JFK Warehouse';

-- New txn_type values so the audit trail (view_storage_history) can tell an
-- inter-warehouse transfer apart from a normal customer-facing inbound/outbound.
ALTER TABLE uchoice_storage_txn DROP CONSTRAINT uchoice_storage_txn_txn_type_check;
ALTER TABLE uchoice_storage_txn ADD CONSTRAINT uchoice_storage_txn_txn_type_check CHECK (txn_type IN
    ('inbound', 'outbound', 'convert_in', 'convert_out',
     'move_in', 'move_out', 'adjust', 'recount',
     'transfer_in', 'transfer_out'));
