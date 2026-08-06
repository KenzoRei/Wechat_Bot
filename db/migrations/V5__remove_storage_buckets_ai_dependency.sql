\encoding UTF8
-- ============================================================
-- V5: Remove AI dependency on storage_buckets for boxes_per_pallet
-- Logistics WeChat Bot Platform
-- Date: 2026-08-06
--
-- boxes_per_pallet resolution (default-fill, multi-bucket clarification,
-- stock-sufficiency check) is now entirely code-level
-- (workflow_engine._resolve_outbound_pallet_defaults +
-- _reject_invalid_outbound_stock), forced to run regardless of the AI's
-- own all_fields_collected judgment (_outbound_required_fields_present).
-- The AI no longer receives real bucket numbers in context at all (see
-- session_manager._build_uchoice_candidates) -- removing the exact
-- material that kept tempting it to self-fill a plausible-looking value
-- despite repeated instructions not to. field_hint simplified to match:
-- its job here is now just "leave it unset if not stated," full stop.
-- ============================================================

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,sku_lines}',
    '"Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}. If the customer does not state boxes_per_pallet for a palletized line, leave it unset entirely -- do not guess, compute, or ask about it. The system resolves it deterministically after your response (auto-fills an unambiguous default, asks a follow-up if genuinely ambiguous, or rejects the request if no valid option exists)."'
)
WHERE name = 'uchoice_outbound_request';
