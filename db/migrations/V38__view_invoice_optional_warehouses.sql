\encoding UTF8
-- ============================================================
-- V38: 费用报告 (view_invoice) -- warehouse optional, default all
-- Logistics WeChat Bot Platform
-- Date: 2026-10-01
--
-- Invoice-inventory plan, Phase 3 (docs/ai-collaboration/
-- 2026-10-invoice-inventory/plan.md, decision D2).
--
-- The warehouse is no longer required: the bot asks only for the month
-- range. warehouse_codes (a list) names one or more warehouses; omitted, the
-- request covers every warehouse the caller may see (all for admin/
-- accountant, the assigned ones for a warehouseman), resolved once when the
-- request runs (core/uchoice_invoice_scope.py). One invoice then covers all
-- of them. The code still reads the old single warehouse_code, so sessions
-- started before this migration keep working.
--
-- Apply AFTER deploying the matching code. The new code works with either
-- schema (with the old one it still asks for a warehouse, and a single code
-- becomes a one-item list). The old code with this schema would run a
-- 费用报告 with no warehouse and return an empty invoice.
-- Idempotent.
-- ============================================================

UPDATE service_type
SET description = '查询一个或多个仓库在指定月份（或范围）内的运营费用汇总（运输/打托/拆包/仓储费）及期初/期末库存——这是仓库整体运营成本报告，不是按客户单独出具的账单。不指定仓库时，默认包含用户有权限的全部仓库。',
    input_schema = '{
        "optional": ["warehouse_codes"],
        "required": ["start_month", "end_month"],
        "field_hints": {
            "start_month": "e.g. 2026-01 — first month of the range (inclusive), month granularity not a free date.",
            "end_month": "e.g. 2026-03 — last month of the range (inclusive). Equal to start_month for a single-month invoice.",
            "warehouse_codes": "List of warehouse codes, e.g. [\"JFK\"] or [\"JFK\", \"DE\"] (JFK, DE, NJ). Omit for all warehouses the user can access; do not ask the user which warehouse."
        }
    }'::jsonb
WHERE name = 'view_invoice';
