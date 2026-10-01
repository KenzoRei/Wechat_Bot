\encoding UTF8
-- ============================================================
-- V36: allow the 'opening' stock-change type
-- Logistics WeChat Bot Platform
-- Date: 2026-10-01
--
-- Invoice-inventory plan, Phase 2b (docs/ai-collaboration/
-- 2026-10-invoice-inventory/plan.md, decisions D5 and D10).
--
-- Each warehouse's starting stock was loaded at go-live (2026-09-01/03) with
-- a 库存盘点 (recount_storage) request, so those rows are tagged 'recount'.
-- The invoice Inventory sheet counts an 'opening' change in the Opening
-- column of its period rather than as a movement, so
-- scripts/relabel_golive_opening.py retags exactly those go-live rows as
-- 'opening' (type and note only; quantities, dates and current stock are
-- untouched). This migration only widens the CHECK constraint to allow it.
--
-- Additive: safe to apply while the previous code runs (it never writes
-- 'opening'). Idempotent.
-- ============================================================

ALTER TABLE uchoice_storage_txn
    DROP CONSTRAINT IF EXISTS uchoice_storage_txn_txn_type_check;

ALTER TABLE uchoice_storage_txn
    ADD CONSTRAINT uchoice_storage_txn_txn_type_check CHECK (txn_type IN (
        'inbound', 'outbound',
        'convert_in', 'convert_out',
        'move_in', 'move_out',
        'adjust', 'recount',
        'transfer_in', 'transfer_out',
        'opening'
    ));
