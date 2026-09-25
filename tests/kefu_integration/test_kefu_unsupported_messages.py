"""
Phase 0 of the Kefu audio-input plan (user decision D4): a non-text message
from authorized staff gets a fixed durable reply and never reaches the AI.
Real PostgreSQL (the delivery enqueue uses ON CONFLICT and the V34 target
constraint).

Isolation: each test creates its own staff identity and inbound rows and
drives only that identity (ready_identities is narrowed), so no other row
in the shared test database is ever claimed or processed. Exact-row
cleanup.
"""
import json
import uuid

import pytest
from sqlalchemy import text

from core import kefu_sync, kefu_unsupported
from core.kefu_contracts import KefuIdentity
from core.kefu_delivery import enqueue_text
from database import SessionLocal
from models.group import GroupConfig
from models.kefu import KefuStaff
from models.role import Role


class _Fixture:
    def __init__(self):
        self.staff_ids = []
        self.msgids = []

    def staff(self, db, role_name="warehouseman", is_active=True):
        role = db.query(Role).filter_by(name=role_name).one()
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        staff = KefuStaff(
            open_kfid=f"kf-unsup-{uuid.uuid4().hex[:8]}",
            external_userid=f"unsup-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id, role_id=role.role_id,
            warehouse_codes=["JFK"] if role_name == "warehouseman" else None,
            is_active=is_active,
        )
        db.add(staff)
        db.flush()
        self.staff_ids.append(staff.staff_id)
        return KefuIdentity(staff.open_kfid, staff.external_userid), staff.staff_id

    def inbound(self, db, identity, msgtype, body=None):
        msgid = f"unsup-{uuid.uuid4().hex}"
        payload = {"msgid": msgid, "open_kfid": identity.open_kfid,
                   "external_userid": identity.external_userid, "msgtype": msgtype, "origin": 3}
        payload[msgtype] = body or {}
        db.execute(text(
            "insert into kefu_inbound_message(msgid,open_kfid,external_userid,payload,status) "
            "values (:m,:o,:e,cast(:p as jsonb),'pending')"
        ), {"m": msgid, "o": identity.open_kfid, "e": identity.external_userid,
            "p": json.dumps(payload)})
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
    f = _Fixture()
    yield f
    f.cleanup()


def _run_only(monkeypatch, identity, processor):
    """run_worker_once, restricted to this test's own identity."""
    monkeypatch.setattr(kefu_sync, "ready_identities", lambda db, limit=100: [identity])
    return kefu_sync.run_worker_once(SessionLocal, processor, worker_id=f"test-{uuid.uuid4().hex[:6]}")


def _deliveries(db, msgid):
    return db.execute(text(
        "select text_content, recipient_staff_id, session_id, request_log_id "
        "from kefu_outbound_delivery where inbound_message_msgid = :m"
    ), {"m": msgid}).all()


def _status(db, msgid):
    return db.execute(text("select status from kefu_inbound_message where msgid=:m"), {"m": msgid}).scalar_one()


def _no_ai_processor(**kwargs):
    raise AssertionError(f"processor (and so the AI) must not run: {kwargs}")


@pytest.mark.parametrize("msgtype,expected", [
    ("image", kefu_unsupported.UNSUPPORTED_REPLY),
    ("file", kefu_unsupported.UNSUPPORTED_REPLY),
    ("video", kefu_unsupported.UNSUPPORTED_REPLY),
    ("location", kefu_unsupported.UNSUPPORTED_REPLY),
    ("voice", kefu_unsupported.VOICE_NOT_YET_REPLY),
])
def test_non_text_from_staff_gets_fixed_reply_and_never_reaches_the_ai(fx, monkeypatch, msgtype, expected):
    db = SessionLocal()
    try:
        identity, staff_id = fx.staff(db)
        msgid = fx.inbound(db, identity, msgtype, {"media_id": "m-1"})
        db.commit()
    finally:
        db.close()

    assert _run_only(monkeypatch, identity, _no_ai_processor) == 1

    db = SessionLocal()
    try:
        rows = _deliveries(db, msgid)
        assert rows == [(expected, staff_id, None, None)]
        assert _status(db, msgid) == "processed"
    finally:
        db.close()


def test_text_messages_still_go_to_the_processor(fx, monkeypatch):
    db = SessionLocal()
    try:
        identity, _ = fx.staff(db)
        msgid = fx.inbound(db, identity, "text", {"content": "查库存"})
        db.commit()
    finally:
        db.close()

    calls = []
    _run_only(monkeypatch, identity, lambda **kw: calls.append(kw["message_content"]))

    db = SessionLocal()
    try:
        assert calls == ["查库存"]
        assert _deliveries(db, msgid) == []
    finally:
        db.close()


@pytest.mark.parametrize("setup", ["unregistered", "suspended", "pending"])
def test_unauthorized_sender_takes_the_existing_denial_path(fx, monkeypatch, setup):
    """No inbox-targeted reply: the normal processor answers them (its
    access/pending checks run before any AI call)."""
    db = SessionLocal()
    try:
        if setup == "unregistered":
            identity = KefuIdentity(f"kf-none-{uuid.uuid4().hex[:8]}", f"none-{uuid.uuid4().hex[:8]}")
        elif setup == "suspended":
            identity, _ = fx.staff(db, is_active=False)
        else:
            identity, _ = fx.staff(db, role_name="pending")
        msgid = fx.inbound(db, identity, "image", {"media_id": "m-1"})
        db.commit()
    finally:
        db.close()

    calls = []
    _run_only(monkeypatch, identity, lambda **kw: calls.append(kw["message_content"]))

    db = SessionLocal()
    try:
        assert calls == [""]
        assert _deliveries(db, msgid) == []
    finally:
        db.close()


def _attempts(db, msgid):
    return db.execute(text("select status, attempt_count from kefu_inbound_message where msgid=:m"), {"m": msgid}).one()


def test_transient_failure_is_retried_not_lost(fx, monkeypatch):
    """Codex Phase 0 audit, P1: a transient error must leave the message
    retryable, and the next pass must deliver the reply."""
    from sqlalchemy.exc import OperationalError
    db = SessionLocal()
    try:
        identity, staff_id = fx.staff(db)
        msgid = fx.inbound(db, identity, "image")
        db.commit()
    finally:
        db.close()

    import core.kefu_delivery as delivery
    real_enqueue = delivery.enqueue_text
    failures = {"left": 1}

    def flaky_enqueue(*args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise OperationalError("insert", {}, Exception("simulated connection drop"))
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(delivery, "enqueue_text", flaky_enqueue)

    _run_only(monkeypatch, identity, _no_ai_processor)
    db = SessionLocal()
    try:
        assert _attempts(db, msgid) == ("pending", 1)
        assert _deliveries(db, msgid) == []
    finally:
        db.close()

    _run_only(monkeypatch, identity, _no_ai_processor)
    db = SessionLocal()
    try:
        assert _attempts(db, msgid) == ("processed", 2)
        assert _deliveries(db, msgid) == [(kefu_unsupported.UNSUPPORTED_REPLY, staff_id, None, None)]
    finally:
        db.close()


def test_persistent_failure_becomes_failed_after_max_attempts(fx, monkeypatch):
    from sqlalchemy.exc import OperationalError
    db = SessionLocal()
    try:
        identity, _ = fx.staff(db)
        msgid = fx.inbound(db, identity, "file")
        db.commit()
    finally:
        db.close()

    import core.kefu_delivery as delivery

    def always_fails(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("still down"))

    monkeypatch.setattr(delivery, "enqueue_text", always_fails)
    for expected in [("pending", 1), ("pending", 2), ("failed", 3)]:
        _run_only(monkeypatch, identity, _no_ai_processor)
        db = SessionLocal()
        try:
            assert _attempts(db, msgid) == expected
        finally:
            db.close()


def test_rehandling_the_same_message_queues_one_reply(fx):
    """A retry after a crash re-enqueues under the same idempotency key."""
    db = SessionLocal()
    try:
        identity, _ = fx.staff(db)
        msgid = fx.inbound(db, identity, "image")
        db.commit()
    finally:
        db.close()

    from core.kefu_contracts import KefuInboundTurn
    from datetime import datetime, timezone
    turn = KefuInboundTurn(identity=identity, msgid=msgid, received_at=datetime.now(timezone.utc),
                           msgtype="image", content=None, payload={}, case_number_hint=None)
    assert kefu_unsupported.handle_if_unsupported(SessionLocal, turn) is True
    assert kefu_unsupported.handle_if_unsupported(SessionLocal, turn) is True

    db = SessionLocal()
    try:
        assert len(_deliveries(db, msgid)) == 1
    finally:
        db.close()


def test_delivery_target_rule_is_exactly_one_of_three(fx):
    db = SessionLocal()
    try:
        identity, staff_id = fx.staff(db)
        msgid = fx.inbound(db, identity, "image")
        db.flush()
        with pytest.raises(ValueError):
            enqueue_text(db, recipient_staff_id=staff_id, idempotency_key=f"none-{msgid}", text_content="x")
        with pytest.raises(ValueError):
            enqueue_text(db, recipient_staff_id=staff_id, idempotency_key=f"two-{msgid}", text_content="x",
                         session_id=uuid.uuid4(), inbound_message_msgid=msgid)
        db.rollback()

        # The database enforces it too (V34), independently of enqueue_text.
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            db.execute(text(
                "insert into kefu_outbound_delivery(recipient_staff_id,idempotency_key,payload_type,text_content,payload_hash) "
                "values (:s,:k,'text','x','h')"
            ), {"s": staff_id, "k": f"raw-{uuid.uuid4().hex}"})
            db.flush()
        db.rollback()
    finally:
        db.close()
