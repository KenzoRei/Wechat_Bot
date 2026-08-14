-- V18: view_storage_history gains an optional export_detail flag.
--
-- Storage-history replies are now capped to the latest 10 movements
-- (core/result_message.py's _STORAGE_HISTORY_DETAIL_LIMIT) -- a full
-- month easily exceeded WeCom Kefu's hard 2048-UTF-8-byte send_text
-- limit, confirmed live as a 100% silent delivery failure for any
-- content-rich reply. export_detail lets a customer explicitly ask for
-- the complete record instead, delivered as a spreadsheet
-- (core/uchoice_storage_history_export.py) rather than crammed into
-- chat text.
--
-- Idempotent for both an existing deployment and a fresh database.

UPDATE service_type
SET input_schema = jsonb_set(
    jsonb_set(
        input_schema,
        '{optional}',
        CASE
            WHEN (input_schema -> 'optional') ? 'export_detail'
                THEN input_schema -> 'optional'
            ELSE COALESCE(input_schema -> 'optional', '[]'::jsonb) || '["export_detail"]'::jsonb
        END
    ),
    '{field_hints}',
    COALESCE(input_schema -> 'field_hints', '{}'::jsonb) || jsonb_build_object(
        'export_detail',
        'Boolean -- true only if the customer explicitly asks for the complete/full record, a detailed list, or a spreadsheet/file export, rather than the standard latest-10-records summary. Do not set unless explicitly requested.'
    )
)
WHERE name = 'view_storage_history';
