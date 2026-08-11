\encoding UTF8
-- ============================================================
-- V12: case_execution.session_id becomes optional
-- Logistics WeChat Bot Platform
-- Date: 2026-08-11
--
-- kefu-migration-plan.md Sec 2.5 / Codex round-92 CAS-and-ledger design:
-- a case_execution row must be claimed BEFORE any writes happen, per
-- msgid -- but for a brand-new case, no conversation_session row exists
-- yet at claim time (it's created inside the same turn this ledger row
-- is tracking). session_id is populated once the session becomes known
-- (core/workflow_engine.py's db_committed hook), in the same commit as
-- the business mutation it certifies -- never required upfront.
-- ============================================================

ALTER TABLE case_execution
    ALTER COLUMN session_id DROP NOT NULL;
