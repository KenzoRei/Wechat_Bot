\encoding UTF8
-- ============================================================
-- V6: view_storage_history — multi-month range support
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- target_month (single "YYYY-MM") replaced with start_month + end_month
-- (both required, inclusive range, same "YYYY-MM" granularity — deliberately
-- NOT free-text dates, keeping the AI-reliable-extraction principle from the
-- original design). A single-month query just sets start_month == end_month.
-- Enables things like "今年一季度出入库情况" -> start_month=2026-01,
-- end_month=2026-03. Requires the AI to know today's date to resolve
-- relative expressions like "今年"/"上个月" — added separately to the
-- system prompt in ai/prompt_builder.py.
-- ============================================================

UPDATE service_type
SET input_schema = '{
    "required": ["warehouse_code", "start_month", "end_month"],
    "optional": [],
    "field_hints": {
        "warehouse_code": "JFK or DE",
        "start_month": "e.g. 2026-01 — first month of the range (inclusive), month granularity not a free date.",
        "end_month": "e.g. 2026-03 — last month of the range (inclusive). Equal to start_month for a single-month query. If it is the current month, results are naturally capped at today since nothing later exists yet."
    }
}'
WHERE name = 'view_storage_history';
