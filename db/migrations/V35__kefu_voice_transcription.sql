\encoding UTF8
-- ============================================================
-- V35: Kefu voice input -- transcript persistence, retry timing, usage alerts
-- Logistics WeChat Bot Platform
-- Date: 2026-09-25
--
-- Kefu audio-input plan, Phase 1 step 2 (docs/ai-collaboration/
-- 2026-09-kefu-audio-input/, Agreed Plan rev 6, approved 2026-09-25).
--
-- kefu_inbound_message gains the persisted transcript for a voice message,
-- committed once, before the AI runs: a retry or lease takeover reuses it
-- instead of paying for, or hearing differently, the same audio. It also
-- gains next_attempt_at, the backoff timer a retryable voice failure puts the
-- row back into 'pending' with (core/kefu_sync.py's claim rule holds that
-- staff member's later messages behind it -- user decision D7).
--
-- kefu_voice_usage_alert is the D6 alert ledger: one row per
-- (UTC date, threshold kind), inserted before logging, so at most one
-- warning per threshold per day across workers and restarts.
-- Idempotent.
-- ============================================================

ALTER TABLE kefu_inbound_message
    ADD COLUMN IF NOT EXISTS transcript              TEXT,
    ADD COLUMN IF NOT EXISTS transcript_status       VARCHAR(20),
    ADD COLUMN IF NOT EXISTS transcript_provider     VARCHAR(64),
    ADD COLUMN IF NOT EXISTS transcribed_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS transcript_duration_ms  INTEGER,
    ADD COLUMN IF NOT EXISTS transcribe_attempts     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_attempt_at         TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'kefu_inbound_message'::regclass
          AND conname = 'ck_kefu_inbound_message_transcript_status'
    ) THEN
        ALTER TABLE kefu_inbound_message
            ADD CONSTRAINT ck_kefu_inbound_message_transcript_status
            CHECK (transcript_status IS NULL OR transcript_status IN ('ok', 'empty', 'failed_terminal'));
    END IF;
END $$;

-- The D6 daily aggregate scans successful transcriptions by day.
CREATE INDEX IF NOT EXISTS idx_kefu_inbound_message_transcribed_at
    ON kefu_inbound_message(transcribed_at)
    WHERE transcript_status = 'ok';

CREATE TABLE IF NOT EXISTS kefu_voice_usage_alert (
    alert_date  DATE        NOT NULL,
    alert_kind  VARCHAR(32) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_date, alert_kind)
);
