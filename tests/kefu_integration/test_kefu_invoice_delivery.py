"""
Real-Postgres coverage: view_invoice via Kefu must send the workbook itself
as a chat file, never a download link -- response_url can't carry a file at
all, and the link/group-webhook path in handlers/uchoice/queries.py's
ComputeInvoiceHandler is Smart Robot-only machinery. core/kefu_turn_apply.py's
_workflow_steps special-cases the seeded "compute_invoice_handler" step for
Kefu turns, building a channel-neutral artifact instead of dispatching to
that handler.
"""
from datetime import datetime, timedelta, timezone

from core import kefu_turn_apply
from core.kefu_artifact_loader import load_artifact
from database import SessionLocal
from models.group import GroupConfig
from models.request_log import RequestLog
from models.service import ServiceType
from models.session import ConversationSession
from models.workflow import Workflow


def _view_invoice_service(db):
    service_type = db.query(ServiceType).filter_by(name="view_invoice").one()
    workflow = db.query(Workflow).filter_by(name="view_invoice").one()
    return {
        "name": "view_invoice",
        "service_type_id": str(service_type.service_type_id),
        "workflow_id": str(workflow.workflow_id),
        "requires_confirmation": False,
        "targets_existing_request": False,
    }


def test_workflow_steps_delivers_invoice_as_kefu_artifact_not_a_link():
    db = SessionLocal()
    session_id = None
    log_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        service = _view_invoice_service(db)
        service_type_id = db.query(ServiceType).filter_by(name="view_invoice").one().service_type_id

        now = datetime.now(timezone.utc)
        month = f"{now.year:04d}-{now.month:02d}"

        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={"warehouse_code": "JFK", "start_month": month, "end_month": month},
            source_channel="kefu",
            expires_at=now + timedelta(minutes=30),
        )
        db.add(session)
        db.flush()
        log = RequestLog(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=service_type_id,
            status="pending",
            raw_message="test",
            source_channel="kefu",
        )
        db.add(log)
        db.flush()
        session.request_log_id = log.log_id
        db.commit()
        session_id, log_id = session.session_id, log.log_id

        context = {"result": {}}
        kefu_turn_apply._workflow_steps(db, context, service, session)

        assert context["result"].get("download_url") is None
        artifacts = context["_kefu_artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["doc_type"] == "invoice_workbook"
        artifact = artifacts[0]["artifact"]
        assert artifact["artifact_key"] == f"{log.log_id}:invoice_workbook"
        assert artifact["content_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert len(artifact["bytes"]) > 0
        assert context["result"]["invoice_artifact_key"] == artifact["artifact_key"]

        # Replay path (deferred delivery / retry) regenerates identical bytes
        # from durable references alone, matching outbound_instruction's
        # existing precedent.
        reloaded = load_artifact(log.log_id, "invoice_workbook", artifact["artifact_key"])
        assert reloaded.artifact_key == artifact["artifact_key"]
        assert len(reloaded.content) > 0
    finally:
        db.rollback()
        if log_id:
            from sqlalchemy import text
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
            db.execute(text("delete from request_log where log_id=:lid"), {"lid": log_id})
        db.commit()
        db.close()
