"""
Codex round-30 finding 1: adjust_storage/move_storage/recount_storage were
still excluded from the DB-phase/side-effect split (their workflows are
just {*_storage_txn -> reply_wechat}, with no generate_pdf_stub/
complete_existing_request step to trigger the original, narrower
eligibility check on). Without the split, reply_wechat ran inside the same
transaction as the storage delta -- a reply failure would roll back an
already-valid, already-computed inventory change purely because the final
WeChat message failed to send. Fixed by switching split eligibility to an
explicit service-name allowlist that includes these three; this proves it.
"""
import pytest
from sqlalchemy import text

from database import SessionLocal
from core import access_control, session_manager, workflow_engine, request_logger

WH = "TESTWHXREPLY"
OPENID = "transworld"
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


_created_session_ids: list = []
_created_log_ids: list = []


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    db.rollback()
    for lid in _created_log_ids:
        db.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
    for sid in _created_session_ids:
        db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": sid})
    _created_session_ids.clear()
    _created_log_ids.clear()
    db.execute(text("delete from uchoice_storage_txn where warehouse_code = :wh"), {"wh": WH})
    db.execute(text("delete from uchoice_storage where warehouse_code = :wh"), {"wh": WH})
    db.commit()


def test_adjust_storage_reply_failure_leaves_inventory_committed(db, monkeypatch):
    existing = db.execute(text(
        "select session_id from conversation_session where wechat_openid = :o "
        "and status in ('active','pending_confirmation')"
    ), {"o": OPENID}).scalar()
    if existing is not None:
        pytest.fail(f"identity already has an active session ({existing})")

    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    db.execute(text("insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count) values (:wh,'s1',80,5)"), {"wh": WH})
    db.commit()

    service_type_id = db.execute(text("select service_type_id from service_type where name = 'adjust_storage'")).scalar()
    session = session_manager.create_session(
        db, wechat_openid=OPENID, group_id=access.group_id,
        initial_message="test", service_type_id=service_type_id,
    )
    _created_session_ids.append(session.session_id)
    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=access.group_id, service_type_id=service_type_id,
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)
    session.request_log_id = log.log_id
    db.commit()
    session_manager.update_collected_fields(db, session, {
        "warehouse_code": WH,
        "adjustment_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_delta": 3, "reason": "reply-failure test"}],
    })
    session.status = "pending_confirmation"
    db.commit()

    context = {
        "wechat_openid": OPENID, "group_id": str(access.group_id), "role": access.role,
        "display_name": access.display_name, "allowed_services": access.allowed_services,
        "session_id": str(session.session_id), "service_type_id": str(service_type_id),
        "collected_fields": session.collected_fields, "serial_number": log.serial_number,
        "response_url": "", "_reply": "",
    }

    class FailingReplyHandler:
        def handle(self, context, config, db):
            raise RuntimeError("simulated reply_wechat failure, after the DB phase already committed")

    monkeypatch.setitem(workflow_engine.HANDLER_REGISTRY, "reply_wechat", FailingReplyHandler)

    workflow_engine._execute_workflow_and_finish(context, session, db)

    db.refresh(session)
    db.refresh(log)
    pallets = db.execute(text(
        "select pallet_count from uchoice_storage where warehouse_code=:wh and sku_code='s1' and boxes_per_pallet=80"
    ), {"wh": WH}).scalar()

    assert pallets == 8, "inventory delta (5+3) must survive a reply_wechat failure -- the DB phase already committed"
    assert log.status == "success", "the operation itself succeeded; a delivery failure must not relabel it"
    assert session.status == "completed"
