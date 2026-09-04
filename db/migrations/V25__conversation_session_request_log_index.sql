-- V25: index conversation_session.request_log_id.
--
-- No index existed on this column. The admin transaction-ledger view
-- queries "every session that touched this request" by request_log_id
-- (not origin_session_id, which only identifies the first session) for
-- every expanded ledger row -- without an index, that becomes a full
-- table scan on a table that grows indefinitely.
--
-- Idempotent for both an existing deployment and a fresh database.

CREATE INDEX IF NOT EXISTS idx_conversation_session_request_log_id
    ON conversation_session (request_log_id);
