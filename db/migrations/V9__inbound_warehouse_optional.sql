\encoding UTF8
-- ============================================================
-- V9: inbound warehouse_code becomes optional, JFK-default
-- Logistics WeChat Bot Platform
-- Date: 2026-08-11
--
-- kefu-migration-plan.md Sec 3 (round 64): the user's final answer
-- extends outbound's existing "explicit when stated, JFK otherwise"
-- warehouse default to uchoice_inbound_request too. warehouse_code moves
-- from input_schema.required to input_schema.optional, matching
-- uchoice_outbound_request's own schema shape -- the new
-- _resolve_inbound_warehouse_default() code (core/workflow_engine.py)
-- fills it in when unstated, same as outbound already does.
-- ============================================================

UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{required}',
        (
            SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
            FROM jsonb_array_elements(input_schema -> 'required') elem
            WHERE elem <> '"warehouse_code"'::jsonb
        )
    ),
    '{optional}',
    COALESCE(input_schema -> 'optional', '[]'::jsonb) || '["warehouse_code"]'::jsonb
)
WHERE name = 'uchoice_inbound_request';
