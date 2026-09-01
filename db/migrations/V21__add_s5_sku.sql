-- V21: add S5 (机器缠绕膜 / Machine Stretch Wrap) to the SKU catalog.
--
-- Idempotent for both an existing deployment and a fresh database.

INSERT INTO uchoice_sku (sku_code, description)
VALUES ('s5', 'S5 Machine Stretch Wrap')
ON CONFLICT (sku_code) DO NOTHING;
