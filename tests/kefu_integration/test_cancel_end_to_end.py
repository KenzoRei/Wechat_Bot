"""
End-to-end regression test for the critical bug an external review found:
core/workflow_engine.py's _execute_workflow_and_finish unconditionally
called request_logger.mark_success() after ANY workflow ran (cancellation
included, since cancel_inbound_request/cancel_outbound_request have
awaits_completion=false, same as most services) -- silently reverting a
just-cancelled request back to 'success' on the Smart Bot channel. The
offline test suite covered the validators and the mark_success guard in
isolation, but not this actual end-to-end path, which is exactly what
missed the regression the first time.

Drives the real _execute_workflow_and_finish (same function a live
cancel_inbound_request confirm turn calls) against a real target request
and real cancel_inbound_request service_type/workflow rows (V23 migration).
Runs against a disposable PostgreSQL database only -- see tests/conftest.py
(auto-marked postgres, auto-skipped without TEST_DATABASE_URL).
"""
import uuid

from sqlalchemy import text

from database import SessionLocal
from models.request_log import RequestLog
from models.session import ConversationSession
from models.service import ServiceType
from models.group import GroupService, GroupConfig
from core.workflow_engine import _execute_workflow_and_finish


def _cleanup(log_id, session_id, target_log_id=None):
    db = SessionLocal()
    try:
        if session_id:
            db.execute(text("delete from conversation_session where session_id=:id"), {"id": session_id})
        if log_id:
            db.execute(text("delete from request_log where log_id=:id"), {"id": log_id})
        if target_log_id and target_log_id != log_id:
            db.execute(text("delete from request_log where log_id=:id"), {"id": target_log_id})
        db.commit()
    finally:
        db.close()


def _allowed_service_entry(db, group_id, service_name):
    service = db.query(ServiceType).filter_by(name=service_name).first()
    assert service is not None, f"{service_name} service_type not found -- V23 migration not applied?"
    grant = db.query(GroupService).filter_by(group_id=group_id, service_type_id=service.service_type_id).first()
    assert grant is not None, f"{service_name} not granted to this group -- V23 migration not applied?"
    return {
        "service_type_id": str(service.service_type_id),
        "name": service.name,
        "workflow_id": str(grant.workflow_id),
        "targets_existing_request": service.targets_existing_request,
        "awaits_completion": service.awaits_completion,
        "requires_confirmation": service.requires_confirmation,
        "group_config": grant.config or {},
    }


def test_smart_robot_cancellation_survives_execute_workflow_and_finish():
    db = SessionLocal()
    target_log_id = None
    session_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        assert group is not None, "no group_config row found -- seed data missing"
        group_id = group.group_id

        inbound_service = db.query(ServiceType).filter_by(name="uchoice_inbound_request").first()
        assert inbound_service is not None

        openid = f"e2e-cancel-{uuid.uuid4().hex[:8]}"
        target = RequestLog(
            wechat_openid=openid,
            group_id=group_id,
            service_type_id=inbound_service.service_type_id,
            status="processing",
            raw_message="e2e cancellation test",
            source_channel="smart_robot",
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        target_log_id = target.log_id

        cancel_service = _allowed_service_entry(db, group_id, "cancel_inbound_request")

        session = ConversationSession(
            wechat_openid=openid,
            group_id=group_id,
            service_type_id=cancel_service["service_type_id"],
            status="pending_confirmation",
            collected_fields={"reference_serial": target.serial_number},
            request_log_id=target_log_id,
            source_channel="smart_robot",
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.session_id

        context = {
            "wechat_openid": openid,
            "group_id": str(group_id),
            "role": "customer",
            "warehouse_codes": None,
            "allowed_services": [cancel_service],
            "source_channel": "smart_robot",
            "submitted_by_staff_id": None,
            "session_id": str(session_id),
            "request_log_id": str(target_log_id),
            "serial_number": target.serial_number,
            "collected_fields": {"reference_serial": target.serial_number},
            "result": {},
        }

        _execute_workflow_and_finish(context, session, db)

        # The critical assertion: status must be 'cancelled', not silently
        # reverted to 'success' by the finalizer that runs after the
        # cancellation handler.
        db.expire_all()
        final = db.query(RequestLog).filter_by(log_id=target_log_id).first()
        assert final.status == "cancelled", (
            f"expected 'cancelled', got '{final.status}' -- the finalizer "
            "overwrote the cancellation (this is the exact regression this "
            "test exists to catch)"
        )
        assert final.result in (None, {}), "cancellation must not populate a completion result"
    finally:
        db.rollback()
        _cleanup(target_log_id, session_id, target_log_id)
        db.close()
