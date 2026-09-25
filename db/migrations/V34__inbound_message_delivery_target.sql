\encoding UTF8
-- ============================================================
-- V34: kefu_outbound_delivery gains a third target, inbound_message_msgid
-- Logistics WeChat Bot Platform
-- Date: 2026-09-25
--
-- Kefu audio-input plan, Phase 0 (docs/ai-collaboration/2026-09-kefu-audio-input/,
-- Agreed Plan rev 6, approved 2026-09-25). A reply to an inbound message that
-- never becomes a case turn -- e.g. "暂不支持该消息类型" for an image/file/voice
-- from an authorized staff member -- has neither a conversation_session nor a
-- request_log to hang a durable delivery row off, and the existing
-- num_nonnulls(session_id, request_log_id) = 1 check forbids a row with
-- neither. The inbound queue row itself is that reply's natural target.
--
-- The V8 target check was created unnamed, so its generated name is looked
-- up by definition rather than assumed; the replacement is named
-- (matching models/kefu.py). Idempotent.
-- ============================================================

ALTER TABLE kefu_outbound_delivery
    ADD COLUMN IF NOT EXISTS inbound_message_msgid VARCHAR(128)
    REFERENCES kefu_inbound_message(msgid);

DO $$
DECLARE
    old_name text;
BEGIN
    FOR old_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'kefu_outbound_delivery'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%num_nonnulls(session_id, request_log_id)%'
          AND pg_get_constraintdef(oid) NOT LIKE '%inbound_message_msgid%'
    LOOP
        EXECUTE format('ALTER TABLE kefu_outbound_delivery DROP CONSTRAINT %I', old_name);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'kefu_outbound_delivery'::regclass
          AND conname = 'ck_kefu_outbound_delivery_target_xor'
    ) THEN
        ALTER TABLE kefu_outbound_delivery
            ADD CONSTRAINT ck_kefu_outbound_delivery_target_xor
            CHECK (num_nonnulls(session_id, request_log_id, inbound_message_msgid) = 1);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kefu_outbound_delivery_inbound_message
    ON kefu_outbound_delivery(inbound_message_msgid)
    WHERE inbound_message_msgid IS NOT NULL;
