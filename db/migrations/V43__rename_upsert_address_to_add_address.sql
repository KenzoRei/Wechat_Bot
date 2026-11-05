\encoding UTF8
-- ============================================================
-- V43: rename the upsert_address service to add_address
-- Logistics WeChat Bot Platform
--
-- Step 1 of the add_address / update_address split (v1.12.0). This is a
-- PURE RENAME: behaviour, descriptions, keywords and confirmation_note are
-- deliberately untouched (the split migration V44 rewrites them). Plan and
-- audit: .collab/tasks/address-add-update-split-plan*.md.
--
-- Renaming rows in place keeps service_type_id / workflow_id, so role
-- grants (role_service_permission), group enablement (group_service),
-- sessions and logs keep pointing at the same rows.
--
-- DO NOT apply this on its own against a running bot. Old code looks the
-- service up as 'upsert_address'; new code as 'add_address'. Run it only
-- inside the paused cutover (suspend service -> V43 -> deploy -> verify ->
-- resume), see CHANGELOG v1.12.0. Rollback = reverse rename + old code.
--
-- Idempotent and fail-closed: a partially migrated or conflicting catalog
-- (both names present, or a name collision) aborts the whole transaction
-- instead of guessing. Contains no COMMIT (the runner / caller owns the
-- transaction).
-- ============================================================

DO $$
DECLARE
    old_service  INT;
    new_service  INT;
    old_workflow INT;
    new_workflow INT;
BEGIN
    SELECT count(*) INTO old_service  FROM service_type WHERE name = 'upsert_address';
    SELECT count(*) INTO new_service  FROM service_type WHERE name = 'add_address';
    SELECT count(*) INTO old_workflow FROM workflow     WHERE name = 'upsert_address';
    SELECT count(*) INTO new_workflow FROM workflow     WHERE name = 'add_address';

    IF old_service > 0 AND new_service > 0 THEN
        RAISE EXCEPTION 'V43: both upsert_address and add_address exist in service_type; refusing to guess';
    END IF;
    IF old_workflow > 0 AND new_workflow > 0 THEN
        RAISE EXCEPTION 'V43: both upsert_address and add_address exist in workflow; refusing to guess';
    END IF;
    IF old_service = 0 AND new_service = 0 THEN
        RAISE EXCEPTION 'V43: neither upsert_address nor add_address exists in service_type';
    END IF;

    UPDATE service_type  SET name      = 'add_address' WHERE name      = 'upsert_address';
    UPDATE workflow      SET name      = 'add_address' WHERE name      = 'upsert_address';
    UPDATE workflow_step SET step_type = 'add_address' WHERE step_type = 'upsert_address';
END $$;
