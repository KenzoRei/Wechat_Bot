"""
Kefu voice input (audio-input plan rev 6, Phase 1) against real PostgreSQL.

Queue behavior: transcript persisted once and reused, retry with backoff and
a one-time notice, terminal failure replies, no paid call for unauthorized
senders, strict per-staff ordering while a voice message waits (D7), and the
D6 usage alert. Safety gate end to end through the real Kefu processor: voice
never executes a confirmation or a system command, and every voice reply
starts with the transcript echo.

No network: media download is a fake client, transcription is patched.
Isolation: synthetic staff/identities/warehouses; the worker is narrowed to
the test's own identity; exact-row cleanup.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

import config
from ai.base import AIResponse
from clients.kefu_client import KefuAPIError
from core import kefu_sync, kefu_voice
from core import voice_transcription as vt
from core.kefu_contracts import KefuIdentity
from database import SessionLocal
from models.group import GroupConfig
from models.kefu import KefuStaff
from models.role import Role
from tests.kefu_integration.test_completion_batch import World, _warehouseman, pallets, seed, storage

VOICE_AUDIO = vt.AMR_MAGIC + (bytes([0x3C]) + bytes(31)) * 250  # 5 s of AMR-NB


class FakeMedia:
    def __init__(self, audio=VOICE_AUDIO, exc=None):
        self.audio, self.exc, self.calls = audio, exc, 0

    def download_media(self, media_id, *, max_bytes):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.audio, "audio/amr"


class Fixture:
    def __init__(self):
        self.staff_ids, self.msgids = [], []

    def staff(self, db, role_name="warehouseman"):
        role = db.query(Role).filter_by(name=role_name).one()
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = KefuStaff(
            open_kfid=f"kf-voice-{uuid.uuid4().hex[:8]}", external_userid=f"voice-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id, role_id=role.role_id,
            warehouse_codes=["JFK"] if role_name == "warehouseman" else None,
        )
        db.add(staff)
        db.flush()
        self.staff_ids.append(staff.staff_id)
        return KefuIdentity(staff.open_kfid, staff.external_userid), staff.staff_id

    def inbound(self, db, identity, msgtype="voice", body=None, seconds_ago=0):
        msgid = f"voice-{uuid.uuid4().hex}"
        payload = {"msgid": msgid, "open_kfid": identity.open_kfid, "external_userid": identity.external_userid,
                   "msgtype": msgtype, "origin": 3, msgtype: body or ({"media_id": "m-1"} if msgtype == "voice" else {})}
        db.execute(text(
            "insert into kefu_inbound_message(msgid,open_kfid,external_userid,payload,status,received_at) "
            "values (:m,:o,:e,cast(:p as jsonb),'pending', now() - make_interval(secs => :ago))"
        ), {"m": msgid, "o": identity.open_kfid, "e": identity.external_userid, "p": json.dumps(payload), "ago": seconds_ago})
        self.msgids.append(msgid)
        return msgid

    def cleanup(self):
        db = SessionLocal()
        try:
            if self.msgids:
                db.execute(text("delete from kefu_outbound_delivery where inbound_message_msgid = any(:m)"), {"m": self.msgids})
                db.execute(text("delete from kefu_inbound_message where msgid = any(:m)"), {"m": self.msgids})
            if self.staff_ids:
                db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id = any(:s)"), {"s": self.staff_ids})
                db.execute(text("delete from kefu_staff where staff_id = any(:s)"), {"s": self.staff_ids})
            db.commit()
        finally:
            db.close()


@pytest.fixture
def fx():
    f = Fixture()
    yield f
    f.cleanup()


def _run(monkeypatch, identity, processor, media):
    monkeypatch.setattr(kefu_sync, "ready_identities", lambda db, limit=100: [identity])
    return kefu_sync.run_worker_once(SessionLocal, processor, worker_id=f"t-{uuid.uuid4().hex[:6]}", media_client=media)


def _transcribe_returns(monkeypatch, text_value="送两箱 S2 去 JFK。", calls=None):
    def fake(audio, *, api_key, model, keywords):
        if calls is not None:
            calls.append(keywords)
        return vt.Transcript(text=text_value, provider=f"openai:{model}", duration_ms=vt.amr_duration_ms(audio))
    monkeypatch.setattr(vt, "transcribe_amr", fake)


def _transcribe_raises(monkeypatch, exc):
    def fake(*args, **kwargs):
        raise exc
    monkeypatch.setattr(vt, "transcribe_amr", fake)


def _row(db, msgid):
    return db.execute(text(
        "select status, transcript, transcript_status, transcript_duration_ms, transcribe_attempts, "
        "next_attempt_at > now() as delayed from kefu_inbound_message where msgid=:m"
    ), {"m": msgid}).mappings().one()


def _replies(db, msgid):
    return db.execute(text(
        "select idempotency_key, text_content, status, last_error from kefu_outbound_delivery "
        "where inbound_message_msgid=:m order by created_at"
    ), {"m": msgid}).all()


def _make_due(db, msgid):
    db.execute(text("update kefu_inbound_message set next_attempt_at = now() - interval '1 second' where msgid=:m"), {"m": msgid})
    db.commit()


def _recorder():
    calls = []

    def processor(**kw):
        calls.append((kw["message_content"], kw["message_meta"].get("input_modality")))
    return processor, calls


def _no_processor(**kw):
    raise AssertionError(f"processor must not run: {kw}")


def _setup(fx, *kinds):
    db = SessionLocal()
    try:
        identity, staff_id = fx.staff(db)
        msgids = [fx.inbound(db, identity, kind, seconds_ago=len(kinds) - i) for i, kind in enumerate(kinds)]
        db.commit()
        return identity, staff_id, msgids
    finally:
        db.close()


# ── Queue behavior ───────────────────────────────────────────────────────────

def test_voice_is_transcribed_once_and_continues_as_text(fx, monkeypatch):
    identity, _, (msgid,) = _setup(fx, "voice")
    keyword_calls = []
    _transcribe_returns(monkeypatch, calls=keyword_calls)
    processor, calls = _recorder()

    _run(monkeypatch, identity, processor, FakeMedia())

    assert calls == [("送两箱 S2 去 JFK。", "voice")]
    assert keyword_calls and all(isinstance(k, str) for k in keyword_calls[0])  # SKU codes passed as hints
    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert (row["status"], row["transcript_status"], row["transcript"]) == ("processed", "ok", "送两箱 S2 去 JFK。")
        assert row["transcript_duration_ms"] == 5000
        assert _replies(db, msgid) == []
    finally:
        db.close()


def test_takeover_reuses_the_persisted_transcript_without_a_second_call(fx, monkeypatch):
    identity, _, (msgid,) = _setup(fx, "voice")
    db = SessionLocal()
    try:
        db.execute(text(
            "update kefu_inbound_message set transcript='出库 S4 两托', transcript_status='ok' where msgid=:m"
        ), {"m": msgid})
        db.commit()
    finally:
        db.close()
    _transcribe_raises(monkeypatch, AssertionError("must not transcribe again"))
    media = FakeMedia(exc=AssertionError("must not download again"))
    processor, calls = _recorder()

    _run(monkeypatch, identity, processor, media)

    assert calls == [("出库 S4 两托", "voice")]
    assert media.calls == 0


def test_retryable_failure_backs_off_with_one_notice_then_succeeds(fx, monkeypatch):
    identity, staff_id, (msgid,) = _setup(fx, "voice")
    _transcribe_raises(monkeypatch, vt.VoiceRetryable("HTTP 503"))
    _run(monkeypatch, identity, _no_processor, FakeMedia())

    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert (row["status"], row["transcribe_attempts"], row["delayed"]) == ("pending", 1, True)
        assert [(k, t, s) for k, t, s, _ in _replies(db, msgid)] == [
            (f"kefu-voice-retry:{msgid}", kefu_voice.RETRY_NOTICE, "pending")]
    finally:
        db.close()

    # Not due yet: nothing is claimed.
    assert _run(monkeypatch, identity, _no_processor, FakeMedia()) == 0

    # A second retryable failure sends no second notice.
    db = SessionLocal()
    try:
        _make_due(db, msgid)
    finally:
        db.close()
    _run(monkeypatch, identity, _no_processor, FakeMedia())
    db = SessionLocal()
    try:
        assert _row(db, msgid)["transcribe_attempts"] == 2
        assert len(_replies(db, msgid)) == 1
        _make_due(db, msgid)
    finally:
        db.close()

    # Then it succeeds: processed as text, and the unsent notice is superseded.
    _transcribe_returns(monkeypatch, "查库存")
    processor, calls = _recorder()
    _run(monkeypatch, identity, processor, FakeMedia())
    assert calls == [("查库存", "voice")]
    db = SessionLocal()
    try:
        assert _row(db, msgid)["status"] == "processed"
        assert [(s, e) for _, _, s, e in _replies(db, msgid)] == [("failed", "superseded")]
    finally:
        db.close()


def test_retries_exhausted_becomes_a_terminal_reply(fx, monkeypatch):
    identity, _, (msgid,) = _setup(fx, "voice")
    _transcribe_raises(monkeypatch, vt.VoiceRetryable("timeout"))
    for _ in range(kefu_voice.MAX_RETRIES + 1):
        _run(monkeypatch, identity, _no_processor, FakeMedia())
        db = SessionLocal()
        try:
            _make_due(db, msgid)
        finally:
            db.close()

    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert (row["status"], row["transcript_status"]) == ("processed", "failed_terminal")
        texts = {k: t for k, t, _, _ in _replies(db, msgid)}
        assert texts[f"kefu-voice:{msgid}"] == kefu_voice.FAILURE_REPLIES["retries_exhausted"]
    finally:
        db.close()


@pytest.mark.parametrize("setup,expected_reply,expected_status", [
    ("expired_media", "语音获取失败，请重新发送或改用文字。", "failed_terminal"),
    ("too_long", "语音超过60秒，请缩短后重新发送或改用文字。", "failed_terminal"),
    ("rejected", "未能识别语音内容，请重新发送或改用文字。", "failed_terminal"),
    ("empty", "未能识别语音内容，请重新发送或改用文字。", "empty"),
])
def test_terminal_failures_reply_once_and_never_reach_the_ai(fx, monkeypatch, setup, expected_reply, expected_status):
    identity, _, (msgid,) = _setup(fx, "voice")
    media = FakeMedia()
    if setup == "expired_media":
        media = FakeMedia(exc=KefuAPIError(40007, "invalid media_id"))
    elif setup == "too_long":
        _transcribe_raises(monkeypatch, vt.VoiceTerminal("too_long", "61000 ms"))
    elif setup == "rejected":
        _transcribe_raises(monkeypatch, vt.VoiceTerminal("provider_rejected", "HTTP 403"))
    else:
        _transcribe_returns(monkeypatch, "")

    _run(monkeypatch, identity, _no_processor, media)

    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert (row["status"], row["transcript_status"]) == ("processed", expected_status)
        assert [(k, t) for k, t, _, _ in _replies(db, msgid)] == [(f"kefu-voice:{msgid}", expected_reply)]
    finally:
        db.close()


def test_unauthorized_sender_is_never_transcribed(fx, monkeypatch):
    identity = KefuIdentity(f"kf-none-{uuid.uuid4().hex[:8]}", f"none-{uuid.uuid4().hex[:8]}")
    db = SessionLocal()
    try:
        msgid = fx.inbound(db, identity, "voice")
        db.commit()
    finally:
        db.close()
    _transcribe_raises(monkeypatch, AssertionError("must not pay for a stranger's audio"))
    media = FakeMedia(exc=AssertionError("must not download a stranger's audio"))
    processor, calls = _recorder()

    _run(monkeypatch, identity, processor, media)

    assert calls == [("", None)]  # the processor's own denial path answers
    assert media.calls == 0


def test_later_messages_wait_behind_a_voice_message_in_backoff(fx, monkeypatch):
    """D7: strict per-staff order."""
    identity, _, (voice_id, text_id) = _setup(fx, "voice", "text")
    _transcribe_raises(monkeypatch, vt.VoiceRetryable("HTTP 503"))
    _run(monkeypatch, identity, _no_processor, FakeMedia())

    # The voice row is delayed; the text behind it must not be claimed.
    processor, calls = _recorder()
    _run(monkeypatch, identity, processor, FakeMedia())
    assert calls == []
    db = SessionLocal()
    try:
        assert _row(db, text_id)["status"] == "pending"
        _make_due(db, voice_id)
    finally:
        db.close()

    # Voice first, then the text, in order.
    _transcribe_returns(monkeypatch, "出库 S2 一托")
    _run(monkeypatch, identity, processor, FakeMedia())
    _run(monkeypatch, identity, processor, FakeMedia())
    assert calls == [("出库 S2 一托", "voice"), ("", None)]


def test_ready_identities_skips_an_identity_held_by_a_delayed_row(fx, monkeypatch):
    identity, _, (voice_id, _text_id) = _setup(fx, "voice", "text")
    _transcribe_raises(monkeypatch, vt.VoiceRetryable("HTTP 503"))
    _run(monkeypatch, identity, _no_processor, FakeMedia())
    monkeypatch.undo()  # restore the real ready_identities
    db = SessionLocal()
    try:
        ready = kefu_sync.ready_identities(db, limit=10_000)
        assert identity not in ready
        _make_due(db, voice_id)
        assert identity in kefu_sync.ready_identities(db, limit=10_000)
    finally:
        db.close()


def test_usage_alert_is_on_by_default_and_zero_disables(monkeypatch):
    """D6 as decided by the user (2026-09-25): on by default at 500 clips /
    60 minutes per day; setting a variable to 0 disables that threshold."""
    import importlib
    try:
        monkeypatch.delenv("VOICE_ALERT_DAILY_CLIPS", raising=False)
        monkeypatch.delenv("VOICE_ALERT_DAILY_MINUTES", raising=False)
        fresh = importlib.reload(config)
        assert (fresh.VOICE_ALERT_DAILY_CLIPS, fresh.VOICE_ALERT_DAILY_MINUTES) == (500, 60)

        monkeypatch.setenv("VOICE_ALERT_DAILY_CLIPS", "0")
        monkeypatch.setenv("VOICE_ALERT_DAILY_MINUTES", "0")
        fresh = importlib.reload(config)
        assert (fresh.VOICE_ALERT_DAILY_CLIPS, fresh.VOICE_ALERT_DAILY_MINUTES) == (0, 0)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_usage_alert_warns_at_most_once_per_day(fx, monkeypatch, capsys):
    db = SessionLocal()
    try:
        db.execute(text("delete from kefu_voice_usage_alert where alert_date = (now() at time zone 'UTC')::date"))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(config, "VOICE_ALERT_DAILY_CLIPS", 1)
    monkeypatch.setattr(config, "VOICE_ALERT_DAILY_MINUTES", 0)
    _transcribe_returns(monkeypatch, "查库存")
    identity, _, _ = _setup(fx, "voice", "voice")
    processor, _ = _recorder()

    _run(monkeypatch, identity, processor, FakeMedia())
    _run(monkeypatch, identity, processor, FakeMedia())

    warnings = [l for l in capsys.readouterr().out.splitlines() if "usage alert" in l and "WARNING" in l]
    assert len(warnings) == 1 and "daily_clips" in warnings[0]
    db = SessionLocal()
    try:
        assert db.execute(text(
            "select count(*) from kefu_voice_usage_alert where alert_date = (now() at time zone 'UTC')::date "
            "and alert_kind = 'daily_clips'"
        )).scalar() == 1
        db.execute(text("delete from kefu_voice_usage_alert where alert_date = (now() at time zone 'UTC')::date"))
        db.commit()
    finally:
        db.close()


def test_without_a_media_client_voice_keeps_the_phase0_reply(fx, monkeypatch):
    identity, _, (msgid,) = _setup(fx, "voice")
    monkeypatch.setattr(kefu_sync, "ready_identities", lambda db, limit=100: [identity])
    kefu_sync.run_worker_once(SessionLocal, _no_processor, worker_id="t-nomedia")
    db = SessionLocal()
    try:
        from core.kefu_unsupported import VOICE_NOT_YET_REPLY
        assert [t for _, t, _, _ in _replies(db, msgid)] == [VOICE_NOT_YET_REPLY]
    finally:
        db.close()


def test_only_read_only_services_skip_confirmation():
    """The voice gate covers every mutation only because every mutating
    service passes through a pending confirmation. Fails if a service is
    ever added with requires_confirmation = false that isn't read-only."""
    db = SessionLocal()
    try:
        names = db.execute(text("select name from service_type where requires_confirmation = false")).scalars().all()
    finally:
        db.close()
    assert names
    assert all(n.startswith("view_") or n == "explain_service" for n in names), names


# ── Recoverability and lost leases (Codex Phase 1 audit) ────────────────────

def _db_error():
    from sqlalchemy.exc import OperationalError
    return OperationalError("stmt", {}, Exception("simulated connection drop"))


@pytest.mark.parametrize("stage", ["persist_transcript", "retry_notice", "terminal_reply"])
def test_database_error_in_the_voice_step_leaves_the_message_recoverable(fx, monkeypatch, stage):
    """P1: never 'failed' on a transient error -- back to pending, delayed."""
    import core.kefu_delivery as delivery
    identity, _, (msgid,) = _setup(fx, "voice")
    if stage == "persist_transcript":
        _transcribe_returns(monkeypatch)
        monkeypatch.setattr(kefu_voice, "_persist_transcript", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    elif stage == "retry_notice":
        _transcribe_raises(monkeypatch, vt.VoiceRetryable("HTTP 503"))
        monkeypatch.setattr(delivery, "enqueue_text", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    else:
        _transcribe_raises(monkeypatch, vt.VoiceTerminal("too_long", "61 s"))
        monkeypatch.setattr(delivery, "enqueue_text", lambda *a, **k: (_ for _ in ()).throw(_db_error()))

    _run(monkeypatch, identity, _no_processor, FakeMedia())

    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert row["status"] == "pending" and row["delayed"] is True
        assert row["transcript_status"] is None
        assert _replies(db, msgid) == []
    finally:
        db.close()

    # Once the database recovers, the message is processed normally.
    monkeypatch.undo()
    _transcribe_returns(monkeypatch, "查库存")
    db = SessionLocal()
    try:
        _make_due(db, msgid)
    finally:
        db.close()
    processor, calls = _recorder()
    _run(monkeypatch, identity, processor, FakeMedia())
    assert calls == [("查库存", "voice")]


def _at_claim_cap(fx, monkeypatch):
    identity, staff_id, (msgid,) = _setup(fx, "voice")
    db = SessionLocal()
    try:
        db.execute(text("update kefu_inbound_message set attempt_count = :n where msgid=:m"),
                   {"n": kefu_voice.MAX_CLAIMS - 1, "m": msgid})
        db.commit()
    finally:
        db.close()
    _transcribe_returns(monkeypatch)
    monkeypatch.setattr(kefu_voice, "_persist_transcript", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    return identity, staff_id, msgid


def test_claim_cap_dead_letters_with_a_staff_reply_and_error_log(fx, monkeypatch, capsys):
    """Round-2 P1: the cap must never end silently."""
    identity, staff_id, msgid = _at_claim_cap(fx, monkeypatch)

    _run(monkeypatch, identity, _no_processor, FakeMedia())  # this claim reaches MAX_CLAIMS

    assert f"ERROR DEAD-LETTER msgid={msgid}" in capsys.readouterr().out
    db = SessionLocal()
    try:
        assert _row(db, msgid)["status"] == "failed"
        assert [(k, t) for k, t, _, _ in _replies(db, msgid)] == [(f"kefu-voice:{msgid}", kefu_voice.DEAD_LETTER_REPLY)]
    finally:
        db.close()


def test_claim_cap_still_ends_the_loop_if_the_reply_cannot_be_queued(fx, monkeypatch, capsys):
    import core.kefu_delivery as delivery
    identity, _, msgid = _at_claim_cap(fx, monkeypatch)
    monkeypatch.setattr(delivery, "enqueue_text", lambda *a, **k: (_ for _ in ()).throw(_db_error()))

    _run(monkeypatch, identity, _no_processor, FakeMedia())

    out = capsys.readouterr().out
    assert f"ERROR DEAD-LETTER msgid={msgid}" in out and "reply could not be queued" in out
    db = SessionLocal()
    try:
        assert _row(db, msgid)["status"] == "failed"  # never held forever (D7)
        assert _replies(db, msgid) == []
    finally:
        db.close()


def test_usage_alert_failure_never_affects_the_turn():
    def broken_factory():
        raise RuntimeError("cannot open a session")
    kefu_voice._check_usage_alert(broken_factory)  # must not raise


@pytest.mark.parametrize("takeover", ["claimed_by_other", "finished_terminally"])
def test_worker_that_lost_its_lease_stops_without_writing(fx, monkeypatch, takeover):
    """P2: persistence requires claim ownership, and the old worker acts on
    the row's real state -- it neither writes a transcript nor processes."""
    identity, _, (msgid,) = _setup(fx, "voice")

    def transcribe_then_lose_lease(audio, *, api_key, model, keywords):
        db = SessionLocal()
        try:
            if takeover == "claimed_by_other":
                db.execute(text("update kefu_inbound_message set claimed_by='other-worker' where msgid=:m"), {"m": msgid})
            else:
                db.execute(text(
                    "update kefu_inbound_message set status='processed', transcript_status='failed_terminal', "
                    "claimed_by='other-worker' where msgid=:m"
                ), {"m": msgid})
            db.commit()
        finally:
            db.close()
        return vt.Transcript(text="出库 S2 一托", provider="openai:m", duration_ms=5000)

    monkeypatch.setattr(vt, "transcribe_amr", transcribe_then_lose_lease)
    _run(monkeypatch, identity, _no_processor, FakeMedia())

    db = SessionLocal()
    try:
        row = _row(db, msgid)
        assert row["transcript"] is None
        if takeover == "claimed_by_other":
            assert (row["status"], row["transcript_status"]) == ("claimed", None)
        else:
            assert (row["status"], row["transcript_status"]) == ("processed", "failed_terminal")
    finally:
        db.close()


# ── Safety gate + echo, end to end through the real Kefu processor ──────────

def _batch_case(world, monkeypatch):
    """A warehouseman with a pending 3-request batch summary."""
    from tests.kefu_integration.test_completion_batch import _ai, _turn
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {10: 5})
        staff = _warehouseman(world, db, w)
        logs = [world.request(db, w, pallets(10, 1, sku)) for _ in range(3)]
        db.commit()
        ids = [l.log_id for l in logs]
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
    finally:
        db.close()
    processor = _ai(monkeypatch, [AIResponse(
        intent="new_request", service_type_name="confirm_outbound_completion_batch",
        extracted_fields={"selection": {"select_all": True}}, all_fields_collected=False, reply="",
    )])
    summary = _turn(processor, identity, "全部确认出库")
    assert "共 3 笔" in summary.reply_text
    return w, sku, identity, ids, summary.case_number


def _voice_turn(processor, identity, transcript, case_number, msgid=None):
    return processor(
        identity=identity, message_content=transcript,
        message_meta={"msgid": msgid or f"voice-{uuid.uuid4().hex}", "input_modality": "voice"},
        case_number_hint=case_number,
    )


class FakeKefuClient:
    """Captures best-effort direct sends (replies with no case to attach to)."""

    def __init__(self):
        self.sent = []

    def get_service_state(self, *, open_kfid, external_userid):
        from types import SimpleNamespace
        return SimpleNamespace(state=1, servicer_userid=None)

    def send_text(self, *, open_kfid, external_userid, text, msgid):
        self.sent.append(text)
        return f"provider-{len(self.sent)}"


def _script_ai(monkeypatch, responses, client=None):
    import core.kefu_case_adapter as adapter
    script = iter(responses)
    monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(script))
    return adapter.make_case_turn_processor(client=client, db_factory=SessionLocal)


def _statuses(ids):
    db = SessionLocal()
    try:
        return [db.execute(text("select status from request_log where log_id=:i"), {"i": i}).scalar_one() for i in ids]
    finally:
        db.close()


@pytest.fixture
def world():
    w = World()
    yield w
    w.cleanup()


def test_voice_numbers_narrow_a_batch_but_never_execute(world, monkeypatch):
    w, sku, identity, ids, case = _batch_case(world, monkeypatch)
    processor = _script_ai(monkeypatch, [])  # deterministic: no AI call

    reply = _voice_turn(processor, identity, "13", case).reply_text

    assert reply.startswith("🎤 识别内容：13")
    assert "共 2 笔" in reply and "请确认以下信息" in reply
    assert _statuses(ids) == ["processing"] * 3
    db = SessionLocal()
    try:
        assert storage(db, w, sku) == {10: 5}
    finally:
        db.close()


def test_voice_confirm_is_gated_and_typed_confirm_still_works(world, monkeypatch):
    w, sku, identity, ids, case = _batch_case(world, monkeypatch)
    confirm = AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=False, reply="")
    processor = _script_ai(monkeypatch, [confirm])

    gated = _voice_turn(processor, identity, "确认", case).reply_text
    assert gated == "🎤 识别内容：确认\n\n语音不能直接确认。请核对上方摘要后，输入文字「确认」。"
    assert _statuses(ids) == ["processing"] * 3

    # The same word typed executes (the batch's exact-affirmative path).
    done = processor(identity=identity, message_content="确认",
                     message_meta={"msgid": f"typed-{uuid.uuid4().hex}"}, case_number_hint=case).reply_text
    assert "已完成 3 笔出库确认" in done
    assert _statuses(ids) == ["success"] * 3


def test_voice_cancel_is_allowed(world, monkeypatch):
    _, _, identity, ids, case = _batch_case(world, monkeypatch)
    processor = _script_ai(monkeypatch, [
        AIResponse(intent="cancel", service_type_name=None, extracted_fields={}, all_fields_collected=False, reply=""),
    ])
    reply = _voice_turn(processor, identity, "取消", case).reply_text
    assert reply.startswith("🎤 识别内容：取消")
    assert "取消" in reply.split("\n\n", 1)[1]
    assert _statuses(ids) == ["processing"] * 3  # cancelling the batch touches no request


def test_voice_replay_returns_the_identical_reply(world, monkeypatch):
    _, _, identity, _, case = _batch_case(world, monkeypatch)
    processor = _script_ai(monkeypatch, [])
    msgid = f"voice-{uuid.uuid4().hex}"
    first = _voice_turn(processor, identity, "13", case, msgid=msgid).reply_text
    again = _voice_turn(processor, identity, "13", case, msgid=msgid).reply_text
    assert again == first


def test_recovered_voice_turn_still_echoes(world, monkeypatch):
    """D2 on the case-execution recovery path (a prior attempt committed
    its business work but never finalized the turn)."""
    from datetime import timedelta
    from models.kefu import CaseExecution
    _, _, identity, _, case = _batch_case(world, monkeypatch)
    msgid = f"voice-{uuid.uuid4().hex}"
    db = SessionLocal()
    try:
        session_id = db.execute(text("select session_id from conversation_session where case_number=:c"),
                                {"c": case}).scalar_one()
        db.add(CaseExecution(
            execution_key=f"kefu:{msgid}", status="db_committed", claimed_by="crashed-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1), session_id=session_id,
            db_committed_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()
    processor = _script_ai(monkeypatch, [])  # recovery never calls the AI

    reply = _voice_turn(processor, identity, "13", case, msgid=msgid).reply_text

    assert reply.startswith("🎤 识别内容：13\n\n您的请求已收到并正在处理")


def test_voice_naming_an_unknown_case_still_echoes(fx, monkeypatch):
    """D2 on the case-resolution denial path (Codex Phase 1 audit, round 3)."""
    db = SessionLocal()
    try:
        identity, _ = fx.staff(db)
        db.commit()
    finally:
        db.close()
    client = FakeKefuClient()
    processor = _script_ai(monkeypatch, [], client=client)
    transcript = "查一下 CASE-20990101-999999"
    processor(identity=identity, message_content=transcript,
              message_meta={"msgid": f"voice-{uuid.uuid4().hex}", "input_modality": "voice"},
              case_number_hint="CASE-20990101-999999")
    assert client.sent == [f"🎤 识别内容：{transcript}\n\n未找到该案件，请核对案件编号。"]


def test_voice_never_triggers_the_admin_purge(fx, monkeypatch):
    from core import kefu_admin_purge
    monkeypatch.setattr(kefu_admin_purge, "_run_purge", lambda db: (_ for _ in ()).throw(AssertionError("purge ran")))
    db = SessionLocal()
    try:
        identity, _ = fx.staff(db, role_name="admin")
        db.commit()
    finally:
        db.close()
    client = FakeKefuClient()
    processor = _script_ai(monkeypatch, [
        AIResponse(intent="unrecognized", service_type_name=None, extracted_fields={}, all_fields_collected=False, reply=""),
    ], client=client)
    reply = _voice_turn(processor, identity, kefu_admin_purge.CONFIRM_COMMAND, None).reply_text
    assert reply.startswith(f"🎤 识别内容：{kefu_admin_purge.CONFIRM_COMMAND}")
    assert "已清空" not in reply
    assert client.sent == [reply]  # answered as an ordinary (unrecognized) message
