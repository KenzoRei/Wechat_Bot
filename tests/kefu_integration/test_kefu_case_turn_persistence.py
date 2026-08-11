"""
Real-Postgres regression tests for Codex round-90 findings 1, 2, and 6.
A mock DB session can't prove any of these -- they're about what actually
survives a commit/session-close, and what two genuinely concurrent
transactions see -- so this uses the real database, same practice as
tests/uchoice_storage_atomicity/.

Finding 1: enqueue_text()'s insert was never committed before
make_case_turn_processor() closed the SQLAlchemy session, so a durable
reply silently vanished. Verified by calling _finalize_turn, closing that
session, then querying kefu_outbound_delivery from a FRESH session.

Finding 2: duplicate-msgid replay read reply_text off the assistant
CaseTurn row, which never had one -- v7 places it on the msgid-bearing
(user) row instead. Verified by querying case_turn directly.

Finding 6: two concurrent transactions must never claim the same pending
completion notice. Verified with two real, simultaneously-open DB
sessions and SELECT ... FOR UPDATE SKIP LOCKED.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
import models.request_log  # noqa: F401 -- registers RequestLog for FK resolution
from core.kefu_case_adapter import _finalize_turn
from core.kefu_completion_notice import lock_pending_completion_notice

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


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


def _real_inbound_service_type_id(db) -> str:
    from models.service import ServiceType
    st = db.query(ServiceType).filter_by(name="uchoice_inbound_request").first()
    assert st is not None, "uchoice_inbound_request service_type not found -- seed data missing"
    return st.service_type_id


def _make_kefu_staff(db, group_id, role_id, *, warehouse_code=None):
    from models.kefu import KefuStaff
    staff = KefuStaff(
        open_kfid=f"kf-test-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-test-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=role_id,
        warehouse_code=warehouse_code,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _make_kefu_session(db, group_id, staff_id):
    from models.session import ConversationSession
    from datetime import datetime, timedelta, timezone
    session = ConversationSession(
        wechat_openid=None,
        group_id=group_id,
        status="active",
        conversation_history=[],
        collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        source_channel="kefu",
        opened_by_staff_id=staff_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@pytest.fixture(autouse=True)
def cleanup():
    created = {"staff_ids": [], "session_ids": []}
    yield created
    db = SessionLocal()
    try:
        for sid in created["session_ids"]:
            db.execute(text("delete from case_turn where session_id = :sid"), {"sid": sid})
            db.execute(text("delete from kefu_outbound_delivery where session_id = :sid"), {"sid": sid})
            db.execute(text("delete from kefu_staff_case_context where active_session_id = :sid"), {"sid": sid})
            db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": sid})
        for stid in created["staff_ids"]:
            db.execute(text("delete from kefu_staff_case_context where staff_id = :stid"), {"stid": stid})
            db.execute(text("delete from kefu_staff where staff_id = :stid"), {"stid": stid})
        db.commit()
    finally:
        db.close()


def test_durable_reply_survives_session_close(db, cleanup):
    """Codex round-90 finding 1."""
    group_id = _real_group_id(db)
    role_id = _real_role_id(db)
    staff = _make_kefu_staff(db, group_id, role_id)
    cleanup["staff_ids"].append(staff.staff_id)
    session = _make_kefu_session(db, group_id, staff.staff_id)
    cleanup["session_ids"].append(session.session_id)

    msgid = f"msgid-persist-{uuid.uuid4().hex[:12]}"
    session_id = session.session_id  # captured before commit/close expires it
    reply_text, case_number, revision = _finalize_turn(
        db, client=None, staff=staff, session_id=session_id,
        message_content="test message", msgid=msgid, reply_text="AI回复内容",
    )
    assert case_number  # a brand-new case got a real case_number
    assert revision == 1
    # _finalize_turn no longer commits itself (Codex round-94: the caller
    # owns the single commit boundary for the whole turn) -- this direct-
    # call test commits on its own behalf to prove persistence.
    db.commit()
    db.close()

    fresh_db = SessionLocal()
    try:
        from models.kefu import KefuOutboundDelivery
        delivery = fresh_db.query(KefuOutboundDelivery).filter_by(
            idempotency_key=f"kefu-reply:{msgid}"
        ).first()
        assert delivery is not None, "durable delivery row did not survive session close"
        assert delivery.status == "pending"
        assert delivery.text_content == "AI回复内容"
        assert delivery.session_id == session_id
    finally:
        fresh_db.close()


def test_replay_payload_lives_on_msgid_bearing_row(db, cleanup):
    """Codex round-90 finding 2."""
    group_id = _real_group_id(db)
    role_id = _real_role_id(db)
    staff = _make_kefu_staff(db, group_id, role_id)
    cleanup["staff_ids"].append(staff.staff_id)
    session = _make_kefu_session(db, group_id, staff.staff_id)
    cleanup["session_ids"].append(session.session_id)

    msgid = f"msgid-replay-{uuid.uuid4().hex[:12]}"
    _finalize_turn(
        db, client=None, staff=staff, session_id=session.session_id,
        message_content="test message", msgid=msgid, reply_text="回复内容用于重放测试",
    )
    db.commit()
    db.close()

    fresh_db = SessionLocal()
    try:
        from models.kefu import CaseTurn
        user_row = fresh_db.query(CaseTurn).filter_by(source_message_id=msgid).first()
        assert user_row is not None
        assert user_row.role == "user"
        assert user_row.reply_text == "回复内容用于重放测试"
    finally:
        fresh_db.close()


def test_concurrent_transactions_never_claim_the_same_notice(db, cleanup):
    """Codex round-90 finding 6 -- the signed simultaneous/no-repeat test."""
    from models.request_log import RequestLog
    from datetime import datetime, timezone

    group_id = _real_group_id(db)
    role_id = _real_role_id(db)
    service_type_id = _real_inbound_service_type_id(db)
    staff = _make_kefu_staff(db, group_id, role_id, warehouse_code="JFK")
    cleanup["staff_ids"].append(staff.staff_id)

    log = RequestLog(
        wechat_openid=None,
        group_id=group_id,
        service_type_id=service_type_id,
        status="success",
        raw_message="test",
        source_channel="kefu",
        result={"warehouse_code": "JFK"},
        completed_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    log_id = log.log_id

    db_a = SessionLocal()
    db_b = SessionLocal()
    try:
        claimed_a = lock_pending_completion_notice(db_a, staff)
        assert claimed_a is not None
        assert claimed_a.log_id == log_id

        # db_a's transaction is still open (no commit yet) -- db_b must see
        # nothing for the same row, not block waiting for the lock.
        claimed_b = lock_pending_completion_notice(db_b, staff)
        assert claimed_b is None

        db_a.rollback()
    finally:
        db_a.close()
        db_b.rollback()
        db_b.close()

    cleanup_db = SessionLocal()
    try:
        cleanup_db.execute(text("delete from request_log where log_id = :lid"), {"lid": log_id})
        cleanup_db.commit()
    finally:
        cleanup_db.close()
