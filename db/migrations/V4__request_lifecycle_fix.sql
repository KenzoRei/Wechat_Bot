\encoding UTF8
-- ============================================================
-- V4: Request Lifecycle Fix — awaits_completion
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- Bug found during live wiring: uchoice_inbound_request/uchoice_outbound_request
-- are two-step services — the customer's confirmation only starts the request;
-- it isn't actually fulfilled until a warehouseman runs
-- confirm_inbound_completion/confirm_outbound_completion against it
-- (targets_existing_request=true). But workflow_engine's
-- _execute_workflow_and_finish unconditionally called mark_success right after
-- the (trivial) record_uchoice_request+reply_wechat workflow steps ran,
-- flipping the log straight from 'pending' to 'success' on confirmation —
-- skipping the long-lived 'processing' state the design doc describes
-- ("processing... long-lived for U-Choice's two-step inbound/outbound flow").
--
-- This column lets workflow_engine distinguish "the workflow steps ran fine"
-- from "this request is actually done" — when true, the log is left at
-- 'processing' after a successful confirm (only the session is closed as
-- completed), and only the later targets_existing_request completion service
-- ever calls mark_success on it.
-- ============================================================

ALTER TABLE service_type
    ADD COLUMN awaits_completion BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE service_type
SET awaits_completion = TRUE
WHERE name IN ('uchoice_inbound_request', 'uchoice_outbound_request');
