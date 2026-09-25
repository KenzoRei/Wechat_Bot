"""
Kefu voice message → persisted transcript, before the AI runs (audio-input
plan rev 6, Phase 1 step 5; user decisions D1-D7, 2026-09-25).

Called by core/kefu_sync.run_worker_once for a claimed msgtype "voice" row,
inside its lease heartbeat, before the case processor:

1. Access pre-check (the processor's own policy): an unregistered,
   suspended, inactive-group or pending sender is never transcribed -- we
   don't pay for strangers' audio. The processor then answers them via its
   existing denial path, with empty content and no AI call.
2. A transcript already persisted for this msgid (retry / lease takeover)
   is reused: no second vendor call, no different wording.
3. Otherwise download → validate → convert → transcribe, and commit the
   transcript in its own transaction (only if none was committed meanwhile).
4. Retryable failure → back to 'pending' with backoff (20 s / 60 s / 3 min,
   at most 3 retries). The row stays the identity's oldest outstanding
   message, so later messages from that staff member wait (D7). The FIRST
   retryable failure also queues the one-time "正在重试" notice, in the same
   transaction.
5. Terminal failure (expired media, not AMR, over 60 s, provider rejection,
   empty transcript, retries exhausted) → a fixed reply and 'processed', in
   one transaction. The AI never sees an empty voice turn.

Every state change is guarded by `status='claimed' AND claimed_by=<this
worker>`, so a worker that lost its lease can't overwrite the new owner.
Replies are durable, targeted at the inbound row (V34). Delivery is
at-least-once, like every Kefu reply. When the voice resolves, a still-unsent
retry notice is marked superseded so it can't arrive late.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.kefu_contracts import KefuInboundTurn

RETRY_BACKOFF = (timedelta(seconds=20), timedelta(seconds=60), timedelta(minutes=3))
MAX_RETRIES = len(RETRY_BACKOFF)

RETRY_NOTICE = "语音识别暂时失败，正在重试，请稍候。您发送的其他消息会按顺序处理。"
FAILURE_REPLIES = {
    "media_unavailable": "语音获取失败，请重新发送或改用文字。",
    "not_amr": "语音获取失败，请重新发送或改用文字。",
    "too_long": "语音超过60秒，请缩短后重新发送或改用文字。",
    "provider_rejected": "未能识别语音内容，请重新发送或改用文字。",
    "empty": "未能识别语音内容，请重新发送或改用文字。",
    "retries_exhausted": "未能识别语音内容，请重新发送或改用文字。",
}

# WeCom errcodes meaning the media itself is gone or invalid (retrying won't help).
_TERMINAL_MEDIA_ERRCODES = {40007}


# Recovery from an unexpected (e.g. database) error in the voice step itself.
RECOVERY_DELAY = timedelta(seconds=30)
# Hard stop for a message that keeps hitting an unexpected error, so it can't
# hold the staff member's queue forever (D7). attempt_count counts claims; a
# healthy voice turn needs at most 1 + MAX_RETRIES of them.
MAX_CLAIMS = 8


@dataclass(frozen=True)
class VoiceOutcome:
    """kind: 'transcript' (continue as text), 'handled' (reply queued, retry
    scheduled, or another worker owns the row -- skip the processor),
    'not_authorized' (let the processor answer with its denial path)."""
    kind: str
    text: str | None = None


def prepare_voice_turn(
    db_factory: Callable[[], Session],
    turn: KefuInboundTurn,
    *,
    media_client,
    worker_id: str,
    transcribe=None,
) -> VoiceOutcome:
    """
    Never raises. Download/transcription failures are classified inside
    _prepare; anything else escaping it (a database error saving the
    transcript or queueing a reply, a bug) must not discard the message
    (Codex Phase 1 audit, P1): the claim is released back to 'pending' for a
    delayed retry, and the processor is skipped this pass.
    """
    try:
        return _prepare(db_factory, turn, media_client=media_client, worker_id=worker_id, transcribe=transcribe)
    except Exception as exc:
        print(f"[kefu_voice] msgid={turn.msgid} unexpected {type(exc).__name__}, releasing for recovery: {exc}", flush=True)
        try:
            _release_for_recovery(db_factory, turn, worker_id, f"{type(exc).__name__}: {exc}")
        except Exception as release_exc:
            # Leave the claim alone: its lease expires and it is reclaimed.
            print(f"[kefu_voice] msgid={turn.msgid} release failed, lease expiry will recover it: {release_exc}", flush=True)
        return VoiceOutcome("handled")


DEAD_LETTER_REPLY = "语音消息处理失败，请重新发送或改用文字。"


def _release_for_recovery(db_factory, turn: KefuInboundTurn, worker_id: str, error: str) -> None:
    """Below the claim cap: back to 'pending' after RECOVERY_DELAY. At the
    cap: dead-letter -- 'failed', plus a staff reply and an ERROR log line,
    so the message never disappears silently (Codex Phase 1 audit round 2)."""
    msgid = turn.msgid
    params = {"e": error[:4000], "m": msgid, "w": worker_id}
    db = db_factory()
    try:
        attempts = db.execute(text(
            "SELECT attempt_count FROM kefu_inbound_message WHERE msgid = :m"
        ), {"m": msgid}).scalar_one()
        if attempts < MAX_CLAIMS:
            db.execute(text(
                "UPDATE kefu_inbound_message SET status = 'pending', "
                "next_attempt_at = now() + make_interval(secs => :delay), "
                "claimed_by = NULL, lease_expires_at = NULL, last_error = :e "
                "WHERE msgid = :m AND status = 'claimed' AND claimed_by = :w"
            ), {**params, "delay": RECOVERY_DELAY.total_seconds()})
            db.commit()
            return
    finally:
        db.close()

    print(f"[kefu_voice] ERROR DEAD-LETTER msgid={msgid} after {attempts} claims: {error[:300]}", flush=True)
    dead_letter_sql = text(
        "UPDATE kefu_inbound_message SET status = 'failed', processed_at = now(), "
        "next_attempt_at = NULL, claimed_by = NULL, lease_expires_at = NULL, last_error = :e "
        "WHERE msgid = :m AND status = 'claimed' AND claimed_by = :w"
    )
    db = db_factory()
    try:
        if db.execute(dead_letter_sql, params).rowcount != 1:
            db.rollback()
            return
        staff_id = _active_staff_id(db, turn.identity)
        if staff_id is not None:
            from core.kefu_delivery import enqueue_text
            enqueue_text(
                db,
                recipient_staff_id=staff_id,
                idempotency_key=f"kefu-voice:{msgid}",
                text_content=DEAD_LETTER_REPLY,
                inbound_message_msgid=msgid,
            )
            _supersede_retry_notice(db, msgid)
        db.commit()
        return
    except Exception as reply_exc:
        db.rollback()
        print(f"[kefu_voice] ERROR DEAD-LETTER msgid={msgid} reply could not be queued: {reply_exc}", flush=True)
    finally:
        db.close()

    # The reply itself failed: still end the loop, so this message can't
    # hold the staff member's queue forever (D7). The ERROR line above is
    # the operator's signal.
    db = db_factory()
    try:
        db.execute(dead_letter_sql, params)
        db.commit()
    finally:
        db.close()


def _active_staff_id(db: Session, identity):
    return db.execute(text(
        "SELECT staff_id FROM kefu_staff WHERE open_kfid = :o AND external_userid = :e AND is_active"
    ), {"o": identity.open_kfid, "e": identity.external_userid}).scalar()


def _prepare(db_factory, turn: KefuInboundTurn, *, media_client, worker_id: str, transcribe) -> VoiceOutcome:
    import config
    from core import access_control, kefu_registration
    from core.voice_transcription import (
        MAX_AUDIO_BYTES, VoiceRetryable, VoiceTerminal, transcribe_amr,
    )
    from clients.kefu_client import KefuAPIError, KefuTransportError

    transcribe = transcribe or transcribe_amr

    db = db_factory()
    try:
        access = access_control.check_kefu_access(db, turn.identity.open_kfid, turn.identity.external_userid)
        if isinstance(access, access_control.AccessDenied):
            return VoiceOutcome("not_authorized")
        if kefu_registration.pending_short_circuit_reply(access.role) is not None:
            return VoiceOutcome("not_authorized")
        staff_id = access.staff_id
        row = db.execute(text(
            "SELECT transcript, transcript_status FROM kefu_inbound_message WHERE msgid = :m"
        ), {"m": turn.msgid}).first()
        if row is not None and row.transcript_status == "ok" and row.transcript:
            return VoiceOutcome("transcript", row.transcript)
        keywords = [r[0] for r in db.execute(text("SELECT sku_code FROM uchoice_sku ORDER BY sku_code")).all()]
    finally:
        db.close()

    media_id = ((turn.payload or {}).get("voice") or {}).get("media_id")
    if not media_id:
        return _finish_terminal(db_factory, turn.msgid, worker_id, staff_id, "media_unavailable", "no media_id in payload")

    try:
        audio, _content_type = media_client.download_media(media_id, max_bytes=MAX_AUDIO_BYTES)
        result = transcribe(
            audio,
            api_key=config.OPENAI_TRANSCRIBE_API_KEY,
            model=config.OPENAI_TRANSCRIBE_MODEL,
            keywords=keywords,
        )
    except VoiceTerminal as exc:
        return _finish_terminal(db_factory, turn.msgid, worker_id, staff_id, exc.reason, str(exc))
    except KefuAPIError as exc:
        if exc.errcode in _TERMINAL_MEDIA_ERRCODES:
            return _finish_terminal(db_factory, turn.msgid, worker_id, staff_id, "media_unavailable", str(exc))
        return _schedule_retry(db_factory, turn.msgid, worker_id, staff_id, str(exc))
    except ValueError as exc:  # download exceeded the byte cap
        return _finish_terminal(db_factory, turn.msgid, worker_id, staff_id, "too_long", str(exc))
    except (VoiceRetryable, KefuTransportError) as exc:
        return _schedule_retry(db_factory, turn.msgid, worker_id, staff_id, str(exc))
    except Exception as exc:  # unexpected: bounded retries, then terminal
        print(f"[kefu_voice] msgid={turn.msgid} unexpected {type(exc).__name__}: {exc}", flush=True)
        return _schedule_retry(db_factory, turn.msgid, worker_id, staff_id, f"{type(exc).__name__}: {exc}")

    if not result.text:
        return _finish_terminal(db_factory, turn.msgid, worker_id, staff_id, "empty", "empty transcript", status="empty")

    outcome = _persist_transcript(db_factory, turn.msgid, worker_id, result)
    if outcome.kind == "transcript":
        _check_usage_alert(db_factory)
    return outcome


def _persist_transcript(db_factory, msgid: str, worker_id: str, result) -> VoiceOutcome:
    """
    Commit the transcript once -- only while this worker still owns the
    claim (Codex Phase 1 audit, P2) -- then act on the row's actual state:
    continue only if this worker still owns a row whose transcript is 'ok'.
    If the lease moved to another worker, or the row was already finished
    (e.g. terminally), this worker does nothing more: the owner decides.
    """
    db = db_factory()
    try:
        db.execute(text(
            "UPDATE kefu_inbound_message SET transcript = :t, transcript_status = 'ok', "
            "transcript_provider = :p, transcribed_at = now(), transcript_duration_ms = :d, last_error = NULL "
            "WHERE msgid = :m AND transcript_status IS NULL AND status = 'claimed' AND claimed_by = :w"
        ), {"t": result.text, "p": result.provider, "d": result.duration_ms, "m": msgid, "w": worker_id})
        row = db.execute(text(
            "SELECT status, claimed_by, transcript, transcript_status FROM kefu_inbound_message WHERE msgid = :m"
        ), {"m": msgid}).mappings().one()
        owns = row["status"] == "claimed" and row["claimed_by"] == worker_id
        if owns and row["transcript_status"] == "ok" and row["transcript"]:
            _supersede_retry_notice(db, msgid)
            db.commit()
            return VoiceOutcome("transcript", row["transcript"])
        db.rollback()
        print(f"[kefu_voice] msgid={msgid} no longer owned by this worker "
              f"(status={row['status']}, transcript_status={row['transcript_status']}); stopping", flush=True)
        return VoiceOutcome("handled")
    finally:
        db.close()


def _schedule_retry(db_factory, msgid: str, worker_id: str, staff_id, error: str) -> VoiceOutcome:
    from core.kefu_delivery import enqueue_text

    db = db_factory()
    try:
        failures = db.execute(text(
            "SELECT transcribe_attempts FROM kefu_inbound_message WHERE msgid = :m"
        ), {"m": msgid}).scalar_one() + 1
        if failures > MAX_RETRIES:
            db.rollback()
            db.close()
            return _finish_terminal(db_factory, msgid, worker_id, staff_id, "retries_exhausted", error)
        delay = RETRY_BACKOFF[failures - 1]
        updated = db.execute(text(
            "UPDATE kefu_inbound_message SET status = 'pending', transcribe_attempts = :f, "
            "next_attempt_at = now() + make_interval(secs => :delay), "
            "claimed_by = NULL, lease_expires_at = NULL, last_error = :e "
            "WHERE msgid = :m AND status = 'claimed' AND claimed_by = :w"
        ), {"f": failures, "delay": delay.total_seconds(), "e": error[:4000], "m": msgid, "w": worker_id}).rowcount
        if updated != 1:
            db.rollback()  # lease lost: the new owner decides
            return VoiceOutcome("handled")
        if failures == 1:
            enqueue_text(
                db,
                recipient_staff_id=staff_id,
                idempotency_key=f"kefu-voice-retry:{msgid}",
                text_content=RETRY_NOTICE,
                inbound_message_msgid=msgid,
            )
        db.commit()
        print(f"[kefu_voice] msgid={msgid} retry {failures}/{MAX_RETRIES} in {delay.total_seconds():.0f}s: {error[:200]}", flush=True)
        return VoiceOutcome("handled")
    finally:
        db.close()


def _finish_terminal(db_factory, msgid: str, worker_id: str, staff_id, reason: str, error: str,
                     *, status: str = "failed_terminal") -> VoiceOutcome:
    from core.kefu_delivery import enqueue_text

    db = db_factory()
    try:
        updated = db.execute(text(
            "UPDATE kefu_inbound_message SET transcript_status = :ts, status = 'processed', processed_at = now(), "
            "next_attempt_at = NULL, lease_expires_at = NULL, last_error = :e "
            "WHERE msgid = :m AND status = 'claimed' AND claimed_by = :w"
        ), {"ts": status, "e": f"{reason}: {error}"[:4000], "m": msgid, "w": worker_id}).rowcount
        if updated != 1:
            db.rollback()
            return VoiceOutcome("handled")
        enqueue_text(
            db,
            recipient_staff_id=staff_id,
            idempotency_key=f"kefu-voice:{msgid}",
            text_content=FAILURE_REPLIES.get(reason, FAILURE_REPLIES["provider_rejected"]),
            inbound_message_msgid=msgid,
        )
        _supersede_retry_notice(db, msgid)
        db.commit()
        print(f"[kefu_voice] msgid={msgid} terminal ({reason}): {error[:200]}", flush=True)
        return VoiceOutcome("handled")
    finally:
        db.close()


def _supersede_retry_notice(db: Session, msgid: str) -> None:
    db.execute(text(
        "UPDATE kefu_outbound_delivery SET status = 'failed', last_error = 'superseded', updated_at = now() "
        "WHERE idempotency_key = :k AND status = 'pending'"
    ), {"k": f"kefu-voice-retry:{msgid}"})


def _check_usage_alert(db_factory) -> None:
    """D6: log-only daily alert, at most one warning per threshold per UTC
    day across workers (the ledger insert is the dedup). Never blocks, and
    never lets its own failure affect the turn."""
    import config

    thresholds = {
        "daily_clips": config.VOICE_ALERT_DAILY_CLIPS,
        "daily_minutes": config.VOICE_ALERT_DAILY_MINUTES,
    }
    if not any(thresholds.values()):
        return
    try:
        db = db_factory()
    except Exception as exc:  # even opening a session must not affect the turn
        print(f"[kefu_voice] usage alert check skipped (ignored): {exc}", flush=True)
        return
    try:
        count, total_ms = db.execute(text(
            "SELECT count(*), coalesce(sum(transcript_duration_ms), 0) FROM kefu_inbound_message "
            "WHERE transcript_status = 'ok' "
            "AND transcribed_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"
        )).one()
        observed = {"daily_clips": count, "daily_minutes": total_ms / 60000}
        for kind, limit in thresholds.items():
            if not limit or observed[kind] < limit:
                continue
            claimed = db.execute(text(
                "INSERT INTO kefu_voice_usage_alert (alert_date, alert_kind) "
                "VALUES ((now() AT TIME ZONE 'UTC')::date, :k) ON CONFLICT DO NOTHING RETURNING alert_kind"
            ), {"k": kind}).first()
            db.commit()
            if claimed is not None:
                print(f"[kefu_voice] WARNING usage alert: {kind} reached {observed[kind]:.1f} (threshold {limit}) today (UTC)", flush=True)
    except Exception as exc:
        db.rollback()
        print(f"[kefu_voice] usage alert check failed (ignored): {exc}", flush=True)
    finally:
        db.close()
