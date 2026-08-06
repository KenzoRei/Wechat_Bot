\encoding UTF8
-- ============================================================
-- V4: view_storage description fix
-- Logistics WeChat Bot Platform
-- Date: 2026-08-06
--
-- Left stale by V3: description still said "可按仓库和/或商品筛选" (filterable
-- by warehouse/SKU) after input_schema was emptied out entirely -- a real
-- mismatch the AI reads every turn, worth fixing even though it wasn't
-- confirmed as the cause of an observed service_type_name=null slip.
-- ============================================================

UPDATE service_type
SET description = '查询两个仓库当前库存余额，立即返回结果，不支持筛选。'
WHERE name = 'view_storage';
