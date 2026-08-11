\encoding UTF8
-- ============================================================
-- V11: pending-completion-notice audience tracking
-- Logistics WeChat Bot Platform
-- Date: 2026-08-11
--
-- kefu-migration-plan.md Sec 7 / Codex round-88 finding 4: a Kefu-
-- originated request's completion (warehouse confirms inbound/outbound)
-- should be surfaced to whichever staff member's next message touches
-- that request's business/warehouse scope, not only the original
-- submitter -- and shown at most once. completion_notice_shown_at is set
-- the moment the notice is actually delivered in a reply (never at
-- completion time itself), so "not yet shown" is exactly NULL.
-- ============================================================

ALTER TABLE request_log
    ADD COLUMN completion_notice_shown_at TIMESTAMPTZ;
