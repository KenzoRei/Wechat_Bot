\encoding UTF8
-- ============================================================
-- V42: optional receiver contact + phone on uchoice_address
-- Logistics WeChat Bot Platform
--
-- Both nullable and free text. Printed on the delivery order "to" card
-- (core/uchoice_delivery_order.py) when present, and accepted by the
-- upsert_address service. NO data backfill here: existing rows that carry
-- a contact/phone inside addr or note are migrated by hand after review,
-- since splitting a phone number out of a free-text street address by
-- regex could corrupt a real address.
-- Idempotent.
-- ============================================================

ALTER TABLE uchoice_address
    ADD COLUMN IF NOT EXISTS contact VARCHAR(100),
    ADD COLUMN IF NOT EXISTS phone   VARCHAR(50);

-- upsert_address: allow the AI to extract the two new optional fields.
-- Defensive against a missing / JSON-null `optional` or `field_hints`
-- (a fresh or hand-edited catalog): both are normalised to empty first.
UPDATE service_type
SET input_schema = jsonb_set(
        jsonb_set(
            input_schema,
            '{optional}',
            (
                SELECT COALESCE(jsonb_agg(DISTINCT v), '[]'::jsonb)
                FROM jsonb_array_elements_text(
                    (CASE WHEN jsonb_typeof(input_schema -> 'optional') = 'array'
                          THEN input_schema -> 'optional' ELSE '[]'::jsonb END)
                    || '["contact","phone"]'::jsonb
                ) AS v
            ),
            true
        ),
        '{field_hints}',
        (CASE WHEN jsonb_typeof(input_schema -> 'field_hints') = 'object'
              THEN input_schema -> 'field_hints' ELSE '{}'::jsonb END)
        || jsonb_build_object(
            'contact', 'Optional — receiver contact person name at this address. Leave unset rather than guessing.',
            'phone',   'Optional — receiver contact phone number at this address. Leave unset rather than guessing.'
        ),
        true
    )
WHERE name = 'upsert_address';
