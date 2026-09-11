"""
Real-Postgres coverage: fedex_label/ups_label via Kefu must send the label
PDF itself as a chat file, same as the invoice/storage-history workbooks
(see test_kefu_invoice_delivery.py) -- unlike those, though, replay must
NEVER call create_label again (a real, one-time carrier API call that
already created an actual shipment); it must read the already-created
label's own bytes back from label_shipment.label_pdf instead.
core/kefu_turn_apply.py's _workflow_steps special-cases
create_fedex_label/create_ups_label for this.
"""
import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from core import customer_directory as cd
from core import kefu_turn_apply
from core.kefu_artifact_loader import load_artifact
from database import SessionLocal
from models.group import GroupConfig
from models.request_log import RequestLog
from models.service import ServiceType
from models.session import ConversationSession
from models.workflow import Workflow

_FAKE_PDF_BYTES = b"%PDF-fake-label-content"


def _fresh_customer_id() -> str:
    return f"F{uuid.uuid4().int % 1_000_000:06d}"


def _fedex_service(db):
    service_type = db.query(ServiceType).filter_by(name="fedex_label").one()
    workflow = db.query(Workflow).filter_by(name="fedex_workorder").one()
    return {
        "name": "fedex_label",
        "service_type_id": str(service_type.service_type_id),
        "workflow_id": str(workflow.workflow_id),
        "requires_confirmation": True,
        "targets_existing_request": False,
    }


def test_workflow_steps_delivers_label_as_kefu_artifact_not_a_link(monkeypatch):
    db = SessionLocal()
    session_id = None
    log_id = None
    customer_id = _fresh_customer_id()
    try:
        cd.upsert_customer(db, customer_id, actor="test", display_name="Kefu Label Delivery Co", status="active")

        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        service = _fedex_service(db)
        service_type_id = db.query(ServiceType).filter_by(name="fedex_label").one().service_type_id

        def fake_create_fedex_label_handle(self, context, config, handler_db):
            return {"tracking_number": "TRACK1", "label_base64": base64.b64encode(_FAKE_PDF_BYTES).decode(), "carrier": "fedex", "sales_amount": None}
        from handlers.registry import HANDLER_REGISTRY
        FakeLabelHandler = type("FakeLabelHandler", (), {"handle": fake_create_fedex_label_handle})
        monkeypatch.setitem(HANDLER_REGISTRY, "create_fedex_label", FakeLabelHandler)

        now = datetime.now(timezone.utc)
        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={"billing_customer_id": customer_id},
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

        context = {"result": {}, "collected_fields": {"billing_customer_id": customer_id}}
        kefu_turn_apply._workflow_steps(db, context, service, session)
        # load_artifact below opens its own fresh DB session/connection --
        # the label_shipment row _record_label_shipment just wrote is
        # invisible there until this transaction actually commits.
        db.commit()

        artifacts = [a for a in context["_kefu_artifacts"] if a["doc_type"] == "label"]
        assert len(artifacts) == 1
        artifact = artifacts[0]["artifact"]
        assert artifact["artifact_key"] == f"{log.log_id}:label"
        assert artifact["content_type"] == "application/pdf"
        assert artifact["bytes"] == _FAKE_PDF_BYTES

        # label_shipment must have the same bytes durably persisted -- the
        # source of truth replay reads from, never create_label again.
        row = db.execute(
            text("select label_pdf, carrier from label_shipment where request_log_id = :rid"),
            {"rid": str(log.log_id)},
        ).first()
        assert row is not None
        assert bytes(row[0]) == _FAKE_PDF_BYTES
        assert row[1] == "fedex"

        # Replay path (deferred delivery / retry) must reproduce the exact
        # same bytes WITHOUT calling create_label again -- HANDLER_REGISTRY
        # is restored to the real handler here (monkeypatch auto-reverts at
        # test end, but this call happens inside the same test), so if
        # load_artifact's "label" branch ever regressed into calling
        # create_label again, this would attempt a real network call and
        # fail loudly rather than silently succeeding with wrong bytes.
        reloaded = load_artifact(log.log_id, "label", artifact["artifact_key"])
        assert reloaded.artifact_key == artifact["artifact_key"]
        assert reloaded.content == _FAKE_PDF_BYTES
    finally:
        db.rollback()
        if log_id:
            db.execute(text("delete from label_shipment where request_log_id=:lid"), {"lid": log_id})
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
            db.execute(text("delete from request_log where log_id=:lid"), {"lid": log_id})
        db.execute(text("delete from customer where customer_id = :cid"), {"cid": customer_id})
        db.commit()
        db.close()


def test_load_artifact_label_raises_without_stored_pdf():
    """If a request_log/session exist but no label_shipment row was ever
    written for it (e.g. the label step never actually ran), replay must
    fail loudly, not silently fabricate an empty/wrong file -- and must
    never fall back to calling create_label again."""
    db = SessionLocal()
    session_id = None
    log_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        service_type_id = db.query(ServiceType).filter_by(name="fedex_label").one().service_type_id
        now = datetime.now(timezone.utc)
        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=service_type_id,
            status="active",
            conversation_history=[],
            collected_fields={},
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

        with pytest.raises(RuntimeError, match="no stored label_pdf"):
            load_artifact(log.log_id, "label", f"{log.log_id}:label")
    finally:
        db.rollback()
        if log_id:
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
            db.execute(text("delete from request_log where log_id=:lid"), {"lid": log_id})
        db.commit()
        db.close()
