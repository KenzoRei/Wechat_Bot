"""
Regression tests for transaction and side-effect boundaries in
core.workflow_engine._execute_workflow_and_finish.

Finding 1: mark_success/close_session used to commit independently of the
storage deltas that preceded them, so a failure between those calls could
leave storage changes durable while the request/session state was
inconsistent. Fixed via commit=False variants folded into one explicit
commit; verified here by injecting a failure between the two and confirming
NOTHING survives (real single-transaction rollback, not just "the deltas
rolled back").

Finding 2: the DB-phase/side-effect-phase split must never apply to a
workflow where the "side effect" steps are actually required operational
work (FedEx/UPS label creation, OMS work orders) -- an empty DB phase would
otherwise mark those requests successful before the label/work order ever
ran. Fixed by only invoking the split for workflows that actually contain
generate_pdf_stub/complete_existing_request; verified here against the real
seeded FedEx workflow shape, with all label/OMS/WeChat clients mocked (no
real network call).
"""
import pytest
from sqlalchemy import text

from database import SessionLocal
import models.request_log  # noqa: F401 -- registers RequestLog for FK resolution
from core import workflow_engine, session_manager, request_logger

WH = "TESTWHXENGINE"
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


def _adjust_storage_session(db, group_id):
    existing = db.execute(text(
        "select session_id from conversation_session where wechat_openid = :o "
        "and status in ('active','pending_confirmation')"
    ), {"o": OPENID}).scalar()
    if existing is not None:
        pytest.fail(f"identity already has an active session ({existing}) -- clean up manually first")

    service_type_id = db.execute(text(
        "select service_type_id from service_type where name = 'adjust_storage'"
    )).scalar()
    session = session_manager.create_session(
        db, wechat_openid=OPENID, group_id=group_id,
        initial_message="test", service_type_id=service_type_id,
    )
    _created_session_ids.append(session.session_id)
    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=group_id, service_type_id=service_type_id,
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)
    session.request_log_id = log.log_id
    db.commit()
    session_manager.update_collected_fields(db, session, {
        "warehouse_code": WH,
        "adjustment_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_delta": 3, "reason": "engine test"}],
    })
    session.status = "pending_confirmation"
    db.commit()
    return session, log, service_type_id


def test_failure_between_mark_success_and_close_session_rolls_back_everything(db, monkeypatch):
    """Finding 1: a failure after mark_success but before the final commit
    must leave NO trace -- not the storage delta, not the log status."""
    from core import access_control
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    db.execute(text("insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count) values (:wh,'s1',80,5)"), {"wh": WH})
    db.commit()

    session, log, service_type_id = _adjust_storage_session(db, access.group_id)

    context = {
        "wechat_openid": OPENID, "group_id": str(access.group_id), "role": access.role,
        "display_name": access.display_name, "allowed_services": access.allowed_services,
        "session_id": str(session.session_id), "service_type_id": str(service_type_id),
        "collected_fields": session.collected_fields, "serial_number": log.serial_number,
        "response_url": "", "_reply": "",
    }

    real_close_session = session_manager.close_session
    call_count = {"n": 0}

    def failing_close_session(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1 and kwargs.get("status") == "completed":
            raise RuntimeError("simulated failure between mark_success and close_session")
        return real_close_session(*args, **kwargs)

    monkeypatch.setattr(workflow_engine.session_manager, "close_session", failing_close_session)

    workflow_engine._execute_workflow_and_finish(context, session, db)

    # if this were still two independent commits (the pre-fix bug), the
    # storage delta and mark_success's status change would already be
    # durable even though close_session blew up right after. With real
    # atomicity, NONE of it should have survived.
    pallets = db.execute(text("select pallet_count from uchoice_storage where warehouse_code=:wh and sku_code='s1' and boxes_per_pallet=80"), {"wh": WH}).scalar()
    db.refresh(log)
    assert pallets == 5, "storage delta must not survive a failure before the final commit"
    assert log.status != "success", "log must not be marked success if the transaction didn't fully commit"


def test_fedex_style_workflow_untouched_by_uchoice_split(db, monkeypatch):
    """Finding 2: a workflow shaped like the real seeded fedex_workorder
    (create_fedex_label -> oms_create_workorder -> reply_wechat, ALL of
    which would have been classified as 'side effects' by the original
    fix) must run as one single phase -- mark_success only after every
    step, including label/work-order creation, has actually succeeded."""
    from core import access_control
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)

    # Confirm against the REAL seeded workflow shape, not a hypothetical one.
    steps = db.execute(text("""
        select ws.step_type from workflow_step ws
        join workflow w on w.workflow_id = ws.workflow_id
        where w.name = 'fedex_workorder'
        order by ws.step_order
    """)).scalars().all()
    assert steps == ["create_fedex_label", "oms_create_workorder", "reply_wechat"], \
        "test assumption about the real fedex_workorder shape is stale -- update this test"

    workflow_id = db.execute(text("select workflow_id from workflow where name='fedex_workorder'")).scalar()

    call_order = []

    class FakeSession:
        def __init__(self):
            self.session_id = "fake-fedex-session"
            self.service_type_id = "fake-fedex-service"
            self.request_log_id = None
            self.status = "pending_confirmation"

    # Monkeypatch the handler registry entries for the fedex steps so no
    # real network call is made, and record the order they actually run in.
    class FakeLabelHandler:
        def handle(self, context, config, db):
            call_order.append("create_fedex_label")
            return {"tracking_number": "FAKE123", "label_url": "https://example.invalid/label.pdf"}

    class FakeWorkorderHandler:
        def handle(self, context, config, db):
            call_order.append("oms_create_workorder")
            return {"oms_order_id": "FAKE-WO-1"}

    class FakeReplyHandler:
        def handle(self, context, config, db):
            call_order.append("reply_wechat")
            return {}

    monkeypatch.setitem(workflow_engine.HANDLER_REGISTRY, "create_fedex_label", FakeLabelHandler)
    monkeypatch.setitem(workflow_engine.HANDLER_REGISTRY, "oms_create_workorder", FakeWorkorderHandler)
    monkeypatch.setitem(workflow_engine.HANDLER_REGISTRY, "reply_wechat", FakeReplyHandler)

    service_type_id = db.execute(text("select service_type_id from service_type where name='fedex_label'")).scalar()
    fake_session_row_service_id = str(service_type_id) if service_type_id else "00000000-0000-0000-0000-000000000000"

    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=access.group_id,
        service_type_id=service_type_id or access.group_id,  # fallback if fedex_label isn't seeded in this group
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)

    # Build a minimal session/context sufficient for _get_workflow_id to
    # resolve to the real fedex_workorder workflow via allowed_services.
    allowed_services = [{
        "name": "fedex_label",
        "service_type_id": fake_session_row_service_id,
        "workflow_id": str(workflow_id),
        "awaits_completion": False,
    }]

    class FakeSessionObj:
        session_id = "fake"
        service_type_id = fake_session_row_service_id
        request_log_id = log.log_id
        status = "pending_confirmation"

    context = {
        "allowed_services": allowed_services,
        "collected_fields": {},
        "serial_number": log.serial_number,
        "response_url": "", "_reply": "",
    }

    workflow_engine._execute_workflow_and_finish(context, FakeSessionObj(), db)

    db.refresh(log)
    assert call_order == ["create_fedex_label", "oms_create_workorder", "reply_wechat"], \
        "all three steps must run, in order, as one phase for a non-U-Choice workflow"
    assert log.status == "success"
    assert log.result.get("tracking_number") == "FAKE123", \
        "the real label output must be stored by mark_success -- proves mark_success ran AFTER label creation, not before"
