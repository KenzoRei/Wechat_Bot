"""
Real-Postgres, real-orchestration-path tests for Codex round-94's explicit
requests: failure-injection through the actual core.kefu_case_adapter
processor (make_case_turn_processor -> _process_turn), a two-worker
overlapping-msgid test, and a cross-staff same-case-revision race test --
none of which a sequential mock or an isolated-helper test can prove.

Architecture under test (round-94's actual fix, not round-93's "mitigated"
version): core/kefu_turn_apply.py builds a turn's business state via
db.add()/db.flush() only, never commits; core/kefu_case_adapter.py's
_process_turn commits EXACTLY ONCE per turn, bundling business state +
execution-ledger completion + case_turn audit + delivery enqueue together.
A crash anywhere before that single commit now leaves NOTHING durable at
all (full rollback) -- there is no more "partially committed" state to
recover from for this read-only-allowlist path, which is a stronger
guarantee than round-93's two-phase design, not merely a smaller version
of the same gap.

The AI provider chain is monkeypatched to a canned, counting stub -- no
real LLM call. view_storage is used as the exercised service: it's in
_KEFU_ENABLED_SERVICES (the rollout gate) and already granted to the
'admin' role on the fixture group, so no new grant seed is needed.
"""
import threading
import time
import uuid

import pytest
from sqlalchemy import text

from ai.base import AIResponse
from database import SessionLocal
import models.request_log  # noqa: F401 -- registers RequestLog for FK resolution
import core.kefu_case_adapter as adapter
import core.kefu_turn_apply as turn_apply
from core.kefu_contracts import CaseTurnSuccess, KefuIdentity

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    from models.group import GroupConfig
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _real_role_id(db, name="admin") -> str:
    from models.role import Role
    role = db.query(Role).filter_by(name=name).first()
    assert role is not None, f"'{name}' role not found -- seed data missing"
    return role.role_id


def _make_staff_row(db, group_id, role_id):
    from models.kefu import KefuStaff
    row = KefuStaff(
        open_kfid=f"kf-crashtest-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-crashtest-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=role_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _cleanup_staff_rows(staff_ids, msgid_prefixes):
    db2 = SessionLocal()
    try:
        for sid in staff_ids:
            db2.execute(text("delete from case_turn where acting_staff_id = :sid"), {"sid": sid})
            db2.execute(text("delete from kefu_outbound_delivery where recipient_staff_id = :sid"), {"sid": sid})
        for pfx in msgid_prefixes:
            db2.execute(text("delete from case_execution where execution_key like :pfx"), {"pfx": f"kefu:{pfx}%"})
        for sid in staff_ids:
            # request_log.origin_session_id FKs to conversation_session --
            # must delete request_log first (recurring lesson this session).
            session_ids = db2.execute(text(
                "select origin_session_id from request_log where submitted_by_staff_id = :sid"
            ), {"sid": sid}).scalars().all()
            db2.execute(text("delete from request_log where submitted_by_staff_id = :sid"), {"sid": sid})
            for s in session_ids:
                if s is not None:
                    db2.execute(text("delete from conversation_session where session_id = :s"), {"s": s})
            db2.execute(text("delete from kefu_staff_case_context where staff_id = :sid"), {"sid": sid})
            db2.execute(text("delete from kefu_staff where staff_id = :sid"), {"sid": sid})
        db2.commit()
    finally:
        db2.close()


@pytest.fixture
def staff():
    row = None
    db = SessionLocal()
    try:
        group_id = _real_group_id(db)
        role_id = _real_role_id(db)
        row = _make_staff_row(db, group_id, role_id)
        yield row
    finally:
        db.close()
        if row is not None:
            _cleanup_staff_rows([row.staff_id], ["crash-", "replay-", "overlap-", "hist-"])


def _canned_ai_response(reply="正在查询库存"):
    return AIResponse(
        intent="new_request",
        reply=reply,
        extracted_fields={},
        all_fields_collected=True,
        service_type_name="view_storage",
    )


def test_crash_before_final_commit_leaves_nothing_durable_and_retry_succeeds_cleanly(monkeypatch, staff):
    """
    Codex round-94: with the real single-transaction fix, a crash anywhere
    before _process_turn's one commit must leave ZERO durable trace (full
    rollback, including the execution-ledger row itself, which was only
    ever flushed, never committed) -- not a "recovered with a generic
    reply" partial state. Retry then runs the full turn cleanly from
    scratch, exactly once, with no duplicate request_log ever possible.
    """
    call_count = {"ai": 0}

    def fake_process(context):
        call_count["ai"] += 1
        return _canned_ai_response()

    monkeypatch.setattr(adapter._ai_chain, "process", fake_process)

    real_finalize = adapter._finalize_turn
    should_crash = {"value": True}

    def crashing_finalize(*args, **kwargs):
        if should_crash["value"]:
            raise RuntimeError("simulated crash before finalize")
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(adapter, "_finalize_turn", crashing_finalize)

    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    identity = KefuIdentity(open_kfid=staff.open_kfid, external_userid=staff.external_userid)
    msgid = f"crash-{uuid.uuid4().hex[:12]}"

    with pytest.raises(RuntimeError, match="simulated crash"):
        processor(identity=identity, message_content="查库存", message_meta={"msgid": msgid}, case_number_hint=None)

    verify_db = SessionLocal()
    try:
        from models.request_log import RequestLog
        from models.kefu import CaseExecution, CaseTurn
        assert verify_db.query(RequestLog).filter_by(wechat_msg_id=msgid).count() == 0, \
            "crash must roll back the whole unit -- no request_log should survive"
        assert verify_db.query(CaseExecution).filter_by(execution_key=f"kefu:{msgid}").first() is None, \
            "the execution-ledger row itself was only flushed, never committed -- must not survive either"
        assert verify_db.query(CaseTurn).filter_by(source_message_id=msgid).count() == 0
    finally:
        verify_db.close()

    assert call_count["ai"] == 1

    # Retry: a genuinely fresh attempt, not a "recovery" -- nothing
    # survived to recover from. The AI runs again, exactly once more.
    should_crash["value"] = False
    result = processor(identity=identity, message_content="查库存", message_meta={"msgid": msgid}, case_number_hint=None)

    assert isinstance(result, CaseTurnSuccess)
    assert call_count["ai"] == 2

    verify_db = SessionLocal()
    try:
        from models.request_log import RequestLog
        from models.kefu import CaseExecution
        logs = verify_db.query(RequestLog).filter_by(wechat_msg_id=msgid).all()
        assert len(logs) == 1, "exactly one request_log from the successful retry"
        execution = verify_db.query(CaseExecution).filter_by(execution_key=f"kefu:{msgid}").first()
        assert execution.status == "completed"
    finally:
        verify_db.close()


def test_duplicate_msgid_after_completion_replays_without_rerunning_ai_or_apply(monkeypatch, staff):
    call_count = {"ai": 0, "apply": 0}

    def fake_process(context):
        call_count["ai"] += 1
        return _canned_ai_response()

    monkeypatch.setattr(adapter._ai_chain, "process", fake_process)

    real_apply = turn_apply.apply_kefu_turn

    def counting_apply(*args, **kwargs):
        call_count["apply"] += 1
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(adapter.kefu_turn_apply, "apply_kefu_turn", counting_apply)

    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    identity = KefuIdentity(open_kfid=staff.open_kfid, external_userid=staff.external_userid)
    msgid = f"replay-{uuid.uuid4().hex[:12]}"

    first = processor(identity=identity, message_content="查库存", message_meta={"msgid": msgid}, case_number_hint=None)
    assert isinstance(first, CaseTurnSuccess)
    assert call_count["ai"] == 1
    assert call_count["apply"] == 1

    second = processor(identity=identity, message_content="查库存", message_meta={"msgid": msgid}, case_number_hint=None)

    assert isinstance(second, CaseTurnSuccess)
    assert second.reply_text == first.reply_text
    assert second.case_number == first.case_number
    assert second.new_revision == first.new_revision
    # Replay must not touch the AI or the turn-apply path a second time.
    assert call_count["ai"] == 1
    assert call_count["apply"] == 1

    verify_db = SessionLocal()
    try:
        from models.kefu import KefuOutboundDelivery
        deliveries = verify_db.query(KefuOutboundDelivery).filter_by(
            idempotency_key=f"kefu-reply:{msgid}"
        ).all()
        assert len(deliveries) == 1, "replay must not enqueue a second delivery"
    finally:
        verify_db.close()


def test_multi_turn_history_ordered_no_duplication_on_replay(monkeypatch, staff):
    """
    Codex round-96 finding 2: the Kefu-native apply path must mirror
    workflow_engine._handle_continuation's conversation_history semantics
    -- append the user's message once per turn, then the assistant's reply
    (clarifying question or final answer) once per turn -- not silently
    drop either side. Uses view_storage_history (requires warehouse_code +
    start_month + end_month) so the first turn is genuinely incomplete and
    a second, continuation turn is required to finish it. A third call
    replays the second turn's msgid and must not append to history again.
    """
    responses = [
        AIResponse(
            intent="new_request", reply="请提供起止月份",
            extracted_fields={"warehouse_code": "JFK"},
            all_fields_collected=False, service_type_name="view_storage_history",
        ),
        AIResponse(
            intent="continuation", reply="",
            extracted_fields={"start_month": "2026-01", "end_month": "2026-01"},
            all_fields_collected=True, service_type_name="view_storage_history",
        ),
    ]
    call_count = {"ai": 0}

    def fake_process(context):
        resp = responses[call_count["ai"]]
        call_count["ai"] += 1
        return resp

    monkeypatch.setattr(adapter._ai_chain, "process", fake_process)

    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    identity = KefuIdentity(open_kfid=staff.open_kfid, external_userid=staff.external_userid)
    msgid_1 = f"hist-1-{uuid.uuid4().hex[:12]}"
    msgid_2 = f"hist-2-{uuid.uuid4().hex[:12]}"

    first = processor(
        identity=identity, message_content="查JFK库存历史",
        message_meta={"msgid": msgid_1}, case_number_hint=None,
    )
    assert isinstance(first, CaseTurnSuccess)
    second = processor(
        identity=identity, message_content="1月到1月",
        message_meta={"msgid": msgid_2}, case_number_hint=None,
    )
    assert isinstance(second, CaseTurnSuccess)
    assert call_count["ai"] == 2

    verify_db = SessionLocal()
    try:
        from models.session import ConversationSession
        session = verify_db.query(ConversationSession).filter_by(case_number=first.case_number).first()
        assert session is not None
        history = session.conversation_history
        assert len(history) == 4, f"expected 4 history entries, got {len(history)}: {history}"
        assert history[0] == {"role": "user", "content": "查JFK库存历史"}
        assert history[1] == {
            "role": "assistant",
            "content": "办理该服务还需要以下信息：\n- 请提供查询的开始月份。\n- 请提供查询的结束月份。",
        }
        assert history[2] == {"role": "user", "content": "1月到1月"}
        assert history[3] == {"role": "assistant", "content": second.reply_text}
    finally:
        verify_db.close()

    # Replay of the already-completed second turn must not touch the AI
    # again, and must not append to history a second time.
    replay = processor(
        identity=identity, message_content="1月到1月",
        message_meta={"msgid": msgid_2}, case_number_hint=None,
    )
    assert isinstance(replay, CaseTurnSuccess)
    assert replay.reply_text == second.reply_text
    assert call_count["ai"] == 2, "replay must not call the AI again"

    verify_db = SessionLocal()
    try:
        from models.session import ConversationSession
        session = verify_db.query(ConversationSession).filter_by(case_number=first.case_number).first()
        assert len(session.conversation_history) == 4, "replay must not append to history again"
    finally:
        verify_db.close()


def test_two_concurrent_attempts_for_the_same_msgid_only_one_creates_the_business_row(monkeypatch, staff):
    """
    Codex round-94's explicit ask: a genuine two-worker overlapping-msgid
    test, not a sequential simulation. Two real threads, each with its own
    DB session, call the processor for the SAME msgid at (as close as
    possible to) the same time. The AI stub sleeps briefly to widen the
    race window between _acquire_execution's advisory lock and the final
    commit.

    Codex round-96 finding 1: the advisory lock (pg_advisory_xact_lock, not
    a try-variant) always BLOCKS a second attempt rather than failing it
    outright, and the replay lookup now runs after acquiring that lock (see
    core/kefu_case_adapter.py's _process_turn). So the only correct outcome
    is "waits, then replays": both attempts must succeed with an identical
    result, and the AI/turn-apply/delivery-enqueue path must each have run
    exactly once -- not "at least one succeeded."
    """
    call_count = {"ai": 0, "apply": 0}

    def slow_ai_process(context):
        # The first thread to reach this pauses briefly, giving the second
        # thread a real chance to race it at the advisory-lock boundary.
        call_count["ai"] += 1
        time.sleep(0.3)
        return _canned_ai_response()

    real_apply = turn_apply.apply_kefu_turn

    def counting_apply(*args, **kwargs):
        call_count["apply"] += 1
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(adapter._ai_chain, "process", slow_ai_process)
    monkeypatch.setattr(adapter.kefu_turn_apply, "apply_kefu_turn", counting_apply)

    identity = KefuIdentity(open_kfid=staff.open_kfid, external_userid=staff.external_userid)
    msgid = f"overlap-{uuid.uuid4().hex[:12]}"
    results = {}
    errors = {}

    def run(label):
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        try:
            results[label] = processor(
                identity=identity, message_content="查库存", message_meta={"msgid": msgid}, case_number_hint=None,
            )
        except Exception as e:
            errors[label] = e

    t1 = threading.Thread(target=run, args=("a",))
    t2 = threading.Thread(target=run, args=("b",))
    t1.start()
    time.sleep(0.05)  # let t1 get slightly ahead so the race is deterministic-ish
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert errors == {}, f"both concurrent attempts must succeed via wait-then-replay, got errors={errors}"
    assert set(results.keys()) == {"a", "b"}
    a, b = results["a"], results["b"]
    assert isinstance(a, CaseTurnSuccess) and isinstance(b, CaseTurnSuccess)
    assert a.reply_text == b.reply_text
    assert a.case_number == b.case_number
    assert a.new_revision == b.new_revision
    assert call_count["ai"] == 1, "AI must run exactly once despite two concurrent attempts"
    assert call_count["apply"] == 1, "turn-apply must run exactly once despite two concurrent attempts"

    verify_db = SessionLocal()
    try:
        from models.request_log import RequestLog
        from models.kefu import KefuOutboundDelivery
        logs = verify_db.query(RequestLog).filter_by(wechat_msg_id=msgid).all()
        assert len(logs) == 1, f"exactly one request_log expected, got {len(logs)}"
        deliveries = verify_db.query(KefuOutboundDelivery).filter_by(
            idempotency_key=f"kefu-reply:{msgid}"
        ).all()
        assert len(deliveries) == 1, "delivery enqueue must occur exactly once despite two concurrent attempts"
    finally:
        verify_db.close()


def test_cross_staff_revision_cas_prevents_lost_update(monkeypatch):
    """
    Codex round-94's explicit ask: a cross-staff same-case race, proving
    the CAS loser leaves no residue. Two DIFFERENT staff members both
    reference the same open case (via case_number_hint) concurrently;
    the guarded UPDATE...WHERE case_revision=:expected in _finalize_turn
    means only one can win, and the loser's whole unit rolls back --
    including its own case_turn/delivery/notice writes, not just the
    revision bump.
    """
    from models.session import ConversationSession

    db = SessionLocal()
    group_id = _real_group_id(db)
    role_id = _real_role_id(db)
    staff_a = _make_staff_row(db, group_id, role_id)
    staff_b = _make_staff_row(db, group_id, role_id)
    # Captured as plain values before db.close() expires the ORM attributes.
    staff_a_id, staff_a_identity = staff_a.staff_id, (staff_a.open_kfid, staff_a.external_userid)
    staff_b_id, staff_b_identity = staff_b.staff_id, (staff_b.open_kfid, staff_b.external_userid)

    # Seed an already-open case both staff can reference by number.
    from datetime import datetime, timedelta, timezone
    session = ConversationSession(
        wechat_openid=None,
        group_id=group_id,
        status="active",
        conversation_history=[],
        collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        source_channel="kefu",
        opened_by_staff_id=staff_a_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    case_number = db.execute(text("SELECT generate_case_number()")).scalar()
    session.case_number = case_number
    db.commit()
    session_id = session.session_id
    db.close()

    def fake_process(context):
        time.sleep(0.3)
        return _canned_ai_response()

    monkeypatch.setattr(adapter._ai_chain, "process", fake_process)

    results = {}
    errors = {}

    def run(label, open_kfid, external_userid):
        identity = KefuIdentity(open_kfid=open_kfid, external_userid=external_userid)
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        msgid = f"cross-staff-{label}-{uuid.uuid4().hex[:8]}"
        try:
            results[label] = processor(
                identity=identity, message_content="查库存",
                message_meta={"msgid": msgid}, case_number_hint=case_number,
            )
        except Exception as e:
            errors[label] = e

    t1 = threading.Thread(target=run, args=("a", *staff_a_identity))
    t2 = threading.Thread(target=run, args=("b", *staff_b_identity))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    try:
        successes = {k: v for k, v in results.items() if isinstance(v, CaseTurnSuccess)}
        # Exactly one side should win the CAS; the other should either
        # error (revision conflict) or -- if it happened to be serialized
        # entirely after the winner via the advisory lock/session-level
        # locking -- also succeed as a legitimate second, later turn on
        # the same case. What must NEVER happen is silent data loss: if
        # only one succeeded, the other must have a recorded error, not a
        # silently-dropped result.
        assert len(successes) >= 1, f"expected at least one success, got results={results} errors={errors}"
        if len(successes) == 1:
            assert len(errors) == 1, "the non-winning side must have errored, not vanished silently"

        verify_db = SessionLocal()
        try:
            from models.kefu import CaseTurn, KefuOutboundDelivery
            from models.session import ConversationSession
            # Codex round-96: tightened from a "<= successes + 1" tolerance
            # (which permitted a rolled-back loser's user turn to survive
            # undetected) to exact equality -- the pre-seeded session has no
            # prior turns, so every persisted msgid-bearing user turn,
            # delivery, and revision increment must correspond 1:1 to an
            # actual successful attempt. A failed CAS attempt must leave
            # ZERO residue: no history/field/delivery trace at all.
            user_turns = verify_db.query(CaseTurn).filter_by(session_id=session_id, role="user").all()
            assert len(user_turns) == len(successes), (
                f"expected exactly {len(successes)} persisted user turn(s) "
                f"(one per successful attempt), got {len(user_turns)}"
            )
            deliveries = verify_db.query(KefuOutboundDelivery).filter_by(session_id=session_id).all()
            assert len(deliveries) == len(successes), (
                f"expected exactly {len(successes)} delivery/deliveries, got {len(deliveries)}"
            )
            final_session = verify_db.get(ConversationSession, session_id)
            assert final_session.case_revision == len(successes), (
                f"expected case_revision == {len(successes)} (one CAS bump per success), "
                f"got {final_session.case_revision}"
            )
            if len(successes) == 1:
                # The CAS loser's own turn must never have landed at all --
                # not even a rolled-back trace of its attempted content.
                losing_msgid_prefixes = [
                    f"cross-staff-{lbl}-" for lbl in results if lbl not in successes
                ]
                for turn in user_turns:
                    assert not any(
                        turn.source_message_id and turn.source_message_id.startswith(pfx)
                        for pfx in losing_msgid_prefixes
                    ), f"a losing CAS attempt's turn leaked into persisted history: {turn.source_message_id}"
        finally:
            verify_db.close()
    finally:
        # conversation_session.opened_by_staff_id FKs to kefu_staff -- must
        # delete the session (and everything referencing it) BEFORE the
        # staff rows, or the staff delete hits a FK violation.
        cleanup_db = SessionLocal()
        try:
            cleanup_db.execute(text("delete from case_turn where session_id = :sid"), {"sid": session_id})
            cleanup_db.execute(text("delete from kefu_outbound_delivery where session_id = :sid"), {"sid": session_id})
            cleanup_db.execute(text("delete from request_log where origin_session_id = :sid"), {"sid": session_id})
            cleanup_db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session_id})
            cleanup_db.commit()
        finally:
            cleanup_db.close()
        _cleanup_staff_rows([staff_a_id, staff_b_id], ["cross-staff-a-", "cross-staff-b-"])
