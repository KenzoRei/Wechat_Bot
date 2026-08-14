"""
Real-PostgreSQL coverage ensuring jobs/session_expiry.py branches notification
by
source_channel, never falling through to the Smart Robot group-webhook path
(which assumes wechat_openid) for a Kefu session.

Deliberately calls _expire_session directly rather than run_expiry_check --
the latter's query is global (every expired session in the database, no
scoping), which is exactly the pattern that accidentally cancelled a real
live Kefu case when a prior test ran it unscoped
(tests/kefu_integration/test_kefu_admin_purge.py's docstring). Calling the
per-session function on rows this test creates itself exercises the same
branching logic with no such blast radius.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from database import SessionLocal
from jobs.session_expiry import _expire_session
from models.group import GroupConfig
from models.kefu import KefuOutboundDelivery, KefuStaff
from models.request_log import RequestLog
from models.role import Role
from models.service import ServiceType
from models.session import ConversationSession


def test_kefu_session_expiry_enqueues_durable_delivery_not_group_webhook(monkeypatch):
    db = SessionLocal()
    staff_id = None
    session_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()

        staff = KefuStaff(
            open_kfid=f"kf-expiry-{uuid.uuid4().hex[:8]}",
            external_userid=f"expiry-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=admin_role.role_id,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.staff_id

        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={},
            source_channel="kefu",
            opened_by_staff_id=staff_id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(session)
        db.flush()
        session_id = session.session_id
        db.commit()

        # conftest.py's autouse block_operational_clients fixture already
        # raises if send_group_webhook_message is called for real -- this
        # test's job is to confirm that path is never reached for Kefu.
        _expire_session(db, session)

        db.refresh(session)
        assert session.status == "timed_out"

        delivery = db.query(KefuOutboundDelivery).filter_by(
            idempotency_key=f"session-expiry:{session_id}"
        ).one_or_none()
        assert delivery is not None, "Kefu session expiry must enqueue a durable delivery"
        assert delivery.recipient_staff_id == staff_id
        assert delivery.session_id == session_id
        assert "None" not in (delivery.text_content or "")
        assert "wechat_openid" not in (delivery.text_content or "").lower()
    finally:
        db.rollback()
        if session_id:
            db.execute(text("delete from kefu_outbound_delivery where session_id=:sid"), {"sid": session_id})
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
        if staff_id:
            db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        db.commit()
        db.close()


def test_kefu_session_with_no_bound_staff_suppresses_notification_without_error():
    """Documented interim rule: no bound staff means no guess at a recipient, not a crash."""
    db = SessionLocal()
    session_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()

        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={},
            source_channel="kefu",
            opened_by_staff_id=None,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(session)
        db.flush()
        session_id = session.session_id
        db.commit()

        _expire_session(db, session)  # must not raise

        db.refresh(session)
        assert session.status == "timed_out"
        assert db.query(KefuOutboundDelivery).filter_by(session_id=session_id).count() == 0
    finally:
        db.rollback()
        if session_id:
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
        db.commit()
        db.close()


def test_kefu_session_with_inactive_staff_suppresses_notification_without_error():
    """A deactivated staff member is treated the same as no bound staff -- no guess at a live recipient."""
    db = SessionLocal()
    staff_id = None
    session_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()

        staff = KefuStaff(
            open_kfid=f"kf-expiry-inactive-{uuid.uuid4().hex[:8]}",
            external_userid=f"expiry-inactive-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=admin_role.role_id,
            is_active=False,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.staff_id

        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={},
            source_channel="kefu",
            opened_by_staff_id=staff_id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(session)
        db.flush()
        session_id = session.session_id
        db.commit()

        _expire_session(db, session)  # must not raise

        db.refresh(session)
        assert session.status == "timed_out"
        assert db.query(KefuOutboundDelivery).filter_by(session_id=session_id).count() == 0
    finally:
        db.rollback()
        if session_id:
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
        if staff_id:
            db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        db.commit()
        db.close()
