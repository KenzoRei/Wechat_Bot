\encoding UTF8
-- ============================================================
-- V3: Stock backstops + service simplification
-- Logistics WeChat Bot Platform
-- Date: 2026-08-06
--
-- Fixes a live crash: sku_lines' field_hint told the AI to compute its own
-- "largest available bucket" default for boxes_per_pallet, which predates
-- the code-level resolver (workflow_engine._resolve_outbound_pallet_defaults)
-- built later to do this deterministically — the AI kept guessing anyway
-- (fabricated boxes_per_pallet=74 for a SKU whose real buckets were 37/64),
-- and since the code-level resolver only fires when the field is fully
-- absent (not present-but-wrong), the bad value sailed through to a crash
-- at completion time. The AI's role in this decision is removed entirely.
--
-- Also two explicit simplifications: view_storage becomes a true
-- zero-argument "show everything" command (no filters, ever), and
-- new_pallet_count stops being proactively asked on every outbound request.
-- ============================================================

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,sku_lines}',
    '"Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}. If the customer does not state boxes_per_pallet for a palletized line, leave it unset -- do NOT guess or compute a default yourself, even using the injected storage_buckets list. The system resolves it deterministically after your response, or rejects the request if no valid default exists."'
)
WHERE name = 'uchoice_outbound_request';

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,new_pallet_count}',
    '"$15/pallet -- customer wants loose boxes consolidated onto a fresh pallet before shipping. Only collect if the customer mentions it unprompted; do not proactively ask. Shown in the confirmation as \"打板数量\"."'
)
WHERE name = 'uchoice_outbound_request';

UPDATE service_type
SET input_schema = '{"required": [], "optional": [], "field_hints": {}}'::jsonb
WHERE name = 'view_storage';
