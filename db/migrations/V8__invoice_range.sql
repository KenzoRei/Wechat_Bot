\encoding UTF8
-- ============================================================
-- V8: view_invoice — multi-month range support
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- Same treatment as V6's view_storage_history change: target_month (single
-- "YYYY-MM") replaced with start_month + end_month (both required,
-- inclusive range). A single-month query just sets start_month == end_month.
-- The monthly scheduled push (jobs/uchoice_invoice.py) is unaffected — it
-- always calls compute_invoice() with a single month, which still works
-- since end_month defaults to start_month.
-- ============================================================

UPDATE service_type
SET input_schema = '{
    "required": ["warehouse_code", "start_month", "end_month"],
    "optional": [],
    "field_hints": {
        "warehouse_code": "JFK or DE",
        "start_month": "e.g. 2026-01 — first month of the range (inclusive), month granularity not a free date.",
        "end_month": "e.g. 2026-03 — last month of the range (inclusive). Equal to start_month for a single-month invoice."
    }
}'
WHERE name = 'view_invoice';
