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
UPDATE service_type
SET input_schema = jsonb_set(
        jsonb_set(
            input_schema,
            '{optional}',
            (
                SELECT COALESCE(jsonb_agg(DISTINCT v), '[]'::jsonb)
                FROM jsonb_array_elements_text(input_schema -> 'optional' || '["contact","phone"]'::jsonb) AS v
            )
        ),
        '{field_hints}',
        (input_schema -> 'field_hints') || jsonb_build_object(
            'contact', 'Optional — receiver contact person name at this address. Leave unset rather than guessing.',
            'phone',   'Optional — receiver contact phone number at this address. Leave unset rather than guessing.'
        )
    )
WHERE name = 'upsert_address';
