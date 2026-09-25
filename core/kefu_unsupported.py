"""
Phase 0 of the Kefu audio-input plan (docs/ai-collaboration/
2026-09-kefu-audio-input/, Agreed Plan rev 6, approved 2026-09-25; user
decision D4): a message that isn't text -- image, file, video, location,
voice (until voice transcription ships), anything else -- gets a fixed
reply instead of reaching the AI.

Before this, core/kefu_sync.claim_next only read content for msgtype
"text", so every other type ran a full AI turn on an empty string.

Only for authorized, operational staff. Everyone else (unregistered,
suspended, inactive group, pending role) falls through to the normal
processor with empty content, whose pre-AI access and pending checks
already answer them without any AI call -- the same path a text message
from them would take.

The reply is durable and targets the inbound row itself (V34's
inbound_message_msgid): there is no case or request to attach it to, and
none is created. Enqueue and "processed" commit in one transaction, so a
crash can't leave a reply without a processed row or the reverse; a retry
after a crash before commit re-enqueues under the same idempotency key.

A failure here (e.g. a transient DB error in the access check or enqueue)
must not lose the message: the row goes back to 'pending' for the worker's
next pass, and only becomes 'failed' after MAX_ATTEMPTS claims (the claim
itself counts attempts). If even that release fails, the row stays claimed
and is reclaimed when its lease expires. (Codex Phase 0 audit, P1.)
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.kefu_contracts import KefuInboundTurn

MAX_ATTEMPTS = 3

UNSUPPORTED_REPLY = "暂不支持该消息类型，请发送文字或语音。"
# Until voice transcription (Phase 1) ships.
VOICE_NOT_YET_REPLY = "暂不支持语音，请发送文字。"


def reply_for(msgtype: str, *, voice_supported: bool = False) -> str | None:
    """None for text, and for voice once transcription is available
    (core/kefu_voice.py handles it); the fixed reply otherwise."""
    if msgtype == "text":
        return None
    if msgtype == "voice":
        return None if voice_supported else VOICE_NOT_YET_REPLY
    return UNSUPPORTED_REPLY


def handle_if_unsupported(
    db_factory: Callable[[], Session], turn: KefuInboundTurn, *, voice_supported: bool = False,
) -> bool:
    """
    Returns True when this turn was dealt with here -- either fully handled
    (reply queued, inbox row processed) or, on a failure, released back to
    'pending' for a retry (or 'failed' after MAX_ATTEMPTS). Returns False
    when the caller should run the normal processor (a text message, or a
    sender who isn't authorized).
    """
    reply = reply_for(turn.msgtype, voice_supported=voice_supported)
    if reply is None:
        return False
    try:
        return _handle(db_factory, turn, reply)
    except Exception as exc:
        print(f"[kefu_unsupported] msgid={turn.msgid} failed, releasing for retry: {exc}", flush=True)
        try:
            _release_for_retry(db_factory, turn.msgid, str(exc))
        except Exception as release_exc:
            # Leave the row claimed: its lease expires and it's reclaimed.
            # Never let the caller mark it 'failed' (terminal) instead.
            print(f"[kefu_unsupported] msgid={turn.msgid} release failed, lease expiry will retry: {release_exc}", flush=True)
        return True


def _release_for_retry(db_factory: Callable[[], Session], msgid: str, error: str) -> None:
    db = db_factory()
    try:
        db.execute(
            text(
                "UPDATE kefu_inbound_message SET "
                "status = CASE WHEN attempt_count < :max THEN 'pending' ELSE 'failed' END, "
                "processed_at = CASE WHEN attempt_count < :max THEN NULL ELSE now() END, "
                "claimed_by = NULL, lease_expires_at = NULL, last_error = :error "
                "WHERE msgid = :msgid AND status = 'claimed'"
            ),
            {"msgid": msgid, "max": MAX_ATTEMPTS, "error": error[:4000]},
        )
        db.commit()
    finally:
        db.close()


def _handle(db_factory: Callable[[], Session], turn: KefuInboundTurn, reply: str) -> bool:
    from core import access_control, kefu_registration
    from core.kefu_delivery import enqueue_text

    db = db_factory()
    try:
        access = access_control.check_kefu_access(
            db, turn.identity.open_kfid, turn.identity.external_userid,
        )
        if isinstance(access, access_control.AccessDenied):
            return False
        if kefu_registration.pending_short_circuit_reply(access.role) is not None:
            return False

        enqueue_text(
            db,
            recipient_staff_id=access.staff_id,
            idempotency_key=f"kefu-unsupported:{turn.msgid}",
            text_content=reply,
            inbound_message_msgid=turn.msgid,
        )
        db.execute(
            text(
                "UPDATE kefu_inbound_message SET status='processed', processed_at=now(), "
                "lease_expires_at=NULL, last_error=NULL WHERE msgid=:msgid"
            ),
            {"msgid": turn.msgid},
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
