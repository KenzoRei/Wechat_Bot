-- V14: deterministic Kefu outbound semantics
--
-- Pallet dimensions on an outbound request describe requested/final packing,
-- not a requirement that source inventory contain the identical bucket.
-- Completion may consume boxes across buckets and, for an internal transfer,
-- records destination packing separately from source picks.

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,sku_lines}',
    to_jsonb(
        'Array of requested outbound lines. Palletized: {sku_code, boxes_per_pallet, pallet_count}; loose: {sku_code, box_count}. boxes_per_pallet describes requested final packing, not a source inventory bucket. Do not infer inventory availability; backend code validates total boxes across the resolved warehouse.'::text
    ),
    true
)
WHERE name = 'uchoice_outbound_request';

UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{optional}',
        CASE
            WHEN (input_schema -> 'optional') ? 'destination_packing_lines'
                THEN input_schema -> 'optional'
            ELSE (input_schema -> 'optional') || '["destination_packing_lines"]'::jsonb
        END,
        true
    ),
    '{field_hints}',
    (input_schema -> 'field_hints') || jsonb_build_object(
        'fulfillment_lines',
        'Actual quantities shipped. State final palletized packing or a loose box_count. Source inventory picks are computed and validated by backend code unless explicitly supplied.',
        'destination_packing_lines',
        'Internal transfers only: positive {sku_code, boxes_per_pallet, pallet_count} lines describing how loose/source-pick-only goods arrive at the destination. Per-SKU box totals must equal actual shipped totals.'
    ),
    true
)
WHERE name = 'confirm_outbound_completion';
