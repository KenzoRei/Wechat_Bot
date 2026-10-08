-- V39: add TL1 (4x6 inch Label) to the SKU catalog.
--
-- Idempotent for both an existing deployment and a fresh database.

INSERT INTO uchoice_sku (sku_code, description)
VALUES ('tl1', 'TL1 4x6 inch Label')
ON CONFLICT (sku_code) DO NOTHING;
