\encoding UTF8
-- ============================================================
-- V16: move_storage description fix
-- Logistics WeChat Bot Platform
--
-- core.kefu_case_adapter's check_services listing renders each service as
-- everything before its FIRST Chinese comma (a short-label heuristic that
-- reads fine for every other service). move_storage's original description
-- happened to put its first comma mid-clause ("仓库人员在同一仓库内，将箱子..."),
-- so check_services showed the meaningless fragment "仓库人员在同一仓库内"
-- instead of describing what the service does at all -- observed live.
-- Full meaning unchanged, just reordered so the first comma lands at a
-- complete, meaningful clause boundary like every other service's does.
-- ============================================================

UPDATE service_type
SET description = '仓库人员将箱子从一个托盘规格调整到另一个（重新打托/合并零散库存），不改变商品总数，操作限于同一仓库内进行。'
WHERE name = 'move_storage';
