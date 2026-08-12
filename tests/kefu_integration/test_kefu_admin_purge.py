"""
Real-Postgres coverage for core/kefu_admin_purge.py: the trigger/confirm
exchange itself must never touch any session (open or otherwise), and the
actual purge must close every open Kefu session + its OWNED request_log
while leaving a targets_existing_request session's REFERENCED log alone,
matching jobs/session_expiry.py's owns_log precedent.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.kefu_admin_purge import CONFIRM_COMMAND, TRIGGER_COMMAND, try_handle_purge_command
from database import SessionLocal
from models.group import GroupConfig
from models.kefu import KefuStaff, KefuStaffCaseContext
from models.request_log import RequestLog
from models.role import Role
from models.service import ServiceType
from models.session import ConversationSession


def _make_session(db, group_id, service_type_id, status, staff_id):
    session = ConversationSession(
        wechat_openid=None,
        group_id=group_id,
        service_type_id=service_type_id,
        status=status,
        conversation_history=[],
        collected_fields={},
        source_channel="kefu",
        opened_by_staff_id=staff_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db.add(session)
    db.flush()
    return session


def _make_log(db, group_id, service_type_id, status):
    log = RequestLog(
        wechat_openid=None,
        group_id=group_id,
        service_type_id=service_type_id,
        status=status,
        raw_message="test",
        source_channel="kefu",
    )
    db.add(log)
    db.flush()
    return log


def test_purge_closes_open_sessions_leaves_targets_existing_request_log_alone():
    db = SessionLocal()
    staff_id = None
    session_ids = []
    log_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()
        completion_type = db.query(ServiceType).filter_by(name="confirm_outbound_completion").one()
        assert completion_type.targets_existing_request is True
        assert outbound_type.targets_existing_request is False

        staff = KefuStaff(
            open_kfid=f"kf-purge-{uuid.uuid4().hex[:8]}",
            external_userid=f"purge-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=admin_role.role_id,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.staff_id

        # 1. Owned, active session -- must be cancelled, log cancelled.
        owned_log = _make_log(db, group.group_id, outbound_type.service_type_id, "pending")
        owned_session = _make_session(db, group.group_id, outbound_type.service_type_id, "active", staff_id)
        owned_session.request_log_id = owned_log.log_id

        # 2. targets_existing_request session -- session cancelled, but the
        #    ORIGINAL request it references must stay untouched.
        original_log = _make_log(db, group.group_id, outbound_type.service_type_id, "processing")
        completion_session = _make_session(
            db, group.group_id, completion_type.service_type_id, "pending_confirmation", staff_id
        )
        completion_session.request_log_id = original_log.log_id

        # 3. Already-closed session -- must be left alone entirely.
        closed_session = _make_session(db, group.group_id, outbound_type.service_type_id, "completed", staff_id)

        db.add(KefuStaffCaseContext(staff_id=staff_id, active_session_id=owned_session.session_id))
        db.commit()

        session_ids = [owned_session.session_id, completion_session.session_id, closed_session.session_id]
        log_ids = [owned_log.log_id, original_log.log_id]

        # Trigger phrase must not touch anything -- purely informational.
        trigger_reply = try_handle_purge_command(db, "admin", TRIGGER_COMMAND)
        assert "2" in trigger_reply or "个" in trigger_reply
        db.refresh(owned_session)
        db.refresh(completion_session)
        assert owned_session.status == "active"
        assert completion_session.status == "pending_confirmation"

        confirm_reply = try_handle_purge_command(db, "admin", CONFIRM_COMMAND)
        assert "已清空" in confirm_reply

        db.refresh(owned_session)
        db.refresh(completion_session)
        db.refresh(closed_session)
        db.refresh(owned_log)
        db.refresh(original_log)

        assert owned_session.status == "cancelled"
        assert owned_log.status == "cancelled"

        assert completion_session.status == "cancelled"
        assert original_log.status == "processing"  # untouched -- not owned by this session

        assert closed_session.status == "completed"  # untouched -- was never open

        binding = db.get(KefuStaffCaseContext, staff_id)
        assert binding.active_session_id is None

        # A second confirm is a safe no-op, not an error.
        second_reply = try_handle_purge_command(db, "admin", CONFIRM_COMMAND)
        assert "没有" in second_reply
    finally:
        db.rollback()
        if staff_id:
            db.execute(text("delete from case_turn where acting_staff_id=:sid"), {"sid": staff_id})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:sid"), {"sid": staff_id})
            db.execute(text("delete from kefu_staff_case_context where staff_id=:sid"), {"sid": staff_id})
        if session_ids:
            db.execute(text("delete from conversation_session where session_id=any(:ids)"), {"ids": session_ids})
        if log_ids:
            db.execute(text("delete from request_log where log_id=any(:ids)"), {"ids": log_ids})
        if staff_id:
            db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        db.commit()
        db.close()


def test_purge_ignores_non_kefu_sessions():
    db = SessionLocal()
    staff_id = None
    session_ids = []
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        admin_role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()

        staff = KefuStaff(
            open_kfid=f"kf-purge2-{uuid.uuid4().hex[:8]}",
            external_userid=f"purge2-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=admin_role.role_id,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.staff_id

        smart_robot_session = ConversationSession(
            wechat_openid="smart-robot-openid",
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={},
            source_channel="smart_robot",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(smart_robot_session)
        db.commit()
        session_ids = [smart_robot_session.session_id]

        reply = try_handle_purge_command(db, "admin", CONFIRM_COMMAND)
        assert "没有" in reply

        db.refresh(smart_robot_session)
        assert smart_robot_session.status == "active"
    finally:
        db.rollback()
        if session_ids:
            db.execute(text("delete from conversation_session where session_id=any(:ids)"), {"ids": session_ids})
        if staff_id:
            db.execute(text("delete from kefu_staff where staff_id=:sid"), {"sid": staff_id})
        db.commit()
        db.close()
