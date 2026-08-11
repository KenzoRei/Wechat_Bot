\encoding UTF8
-- ============================================================
-- V6: outbound pickup/delivery instruction PDF timing
-- Logistics WeChat Bot Platform
-- Date: 2026-08-10
--
-- Phase 3 (docs/ai-collaboration/phase3-outbound-pdf-timing.md, signed by
-- both agents, user-approved): the pickup/delivery instruction PDF must be
-- generated when the customer's uchoice_outbound_request is created and
-- confirmed, not later when a warehouseman confirms
-- confirm_outbound_completion. Moves the generate_pdf_stub step between the
-- two workflows accordingly. Depends on Phase 2's DB-phase/post-commit
-- engine split (core/workflow_engine.py _SIDE_EFFECT_STEP_TYPES), already
-- landed, so this new request-time PDF step runs post-commit without a
-- failure there marking the otherwise-successful request as failed.
-- ============================================================

-- Remove the completion-time instruction PDF step from
-- confirm_outbound_completion, and close the step_order gap it leaves.
DELETE FROM workflow_step
WHERE workflow_id = 'c2000000-0000-0000-0000-000000000004'
  AND step_type = 'generate_pdf_stub';

UPDATE workflow_step
SET step_order = step_order - 1
WHERE workflow_id = 'c2000000-0000-0000-0000-000000000004'
  AND step_order > 3;

-- Make room for the new request-time PDF step in uchoice_outbound_request's
-- workflow, between record_uchoice_request (1) and reply_wechat (2).
UPDATE workflow_step
SET step_order = step_order + 1
WHERE workflow_id = 'c2000000-0000-0000-0000-000000000002'
  AND step_order >= 2;

INSERT INTO workflow_step (workflow_id, step_order, step_type, config)
VALUES (
    'c2000000-0000-0000-0000-000000000002',
    2,
    'generate_pdf_stub',
    '{"doc_type": "outbound_instruction"}'::jsonb
);
