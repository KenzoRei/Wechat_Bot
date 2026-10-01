\encoding UTF8
-- ============================================================
-- V37: stored bytes for Kefu file artifacts built from live data
-- Logistics WeChat Bot Platform
-- Date: 2026-10-01
--
-- Invoice-inventory plan, Phase 2 "Replay stability" (docs/ai-collaboration/
-- 2026-10-invoice-inventory/plan.md, Codex audit #4, decisions D8/D9).
--
-- core/kefu_delivery.py sends a file by re-loading it on every attempt and
-- checking its hash against the one recorded at enqueue. V28's assumption
-- that an invoice workbook is a pure function of stored data is false: any
-- range including the current month changes when a request completes or the
-- daily storage-fee row is written, and the new Inventory sheet reads stock
-- history that a later correction can change. The storage-history export has
-- the same problem for any range including the current month. A rebuilt file
-- then fails the hash check (artifact_hash_mismatch) and the delivery is lost.
--
-- enqueue_file now stores the exact bytes once, keyed by artifact_key, for
-- these doc types (invoice_workbook, storage_history_workbook), and
-- core/kefu_artifact_loader.py reads them back instead of rebuilding.
-- Retention (D8): a row is purged once it is over 30 days old and no delivery
-- of it is pending (core/kefu_delivery.py purge_expired_artifact_blobs,
-- scheduled daily in main.py). A duplicate-message replay never rebuilds or
-- fails on a purged file (D9).
--
-- Additive: safe to apply while the previous code runs. Idempotent.
-- ============================================================

CREATE TABLE IF NOT EXISTS kefu_artifact_blob (
    artifact_key  VARCHAR(200) PRIMARY KEY,
    content       BYTEA        NOT NULL,
    filename      TEXT         NOT NULL,
    content_type  TEXT         NOT NULL,
    content_hash  VARCHAR(64)  NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
