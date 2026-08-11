"""
Phase 3 (phase3-outbound-pdf-timing.md) verification: the outbound pickup/
delivery instruction PDF is generated at request-creation time, from the
request's own validated data, using a stable persisted date (not the wall
clock) -- and confirm_outbound_completion no longer generates it.

Real Postgres DB (workflow_step ordering and RequestLog.created_at are
exactly what's under test -- a mock can't stand in for them). Fail-closed,
exact-row cleanup per the established pattern; never bulk-deletes by the
shared test identity.
"""
import datetime
import pytest
from sqlalchemy import text

from database import SessionLocal
from handlers.uchoice.pdf_stub import GeneratePdfStubHandler

WH = "TESTWHXPDF"
OPENID = "transworld"
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


_created_log_ids: list = []


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    db.rollback()
    for lid in _created_log_ids:
        db.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
    _created_log_ids.clear()
    db.commit()


def test_workflow_step_ordering_matches_phase3_target(db):
    rows = db.execute(text("""
        select st.name as service, ws.step_order, ws.step_type, ws.config
        from workflow_step ws
        join group_service gs on gs.workflow_id = ws.workflow_id
        join service_type st on st.service_type_id = gs.service_type_id
        where st.name in ('uchoice_outbound_request','confirm_outbound_completion')
        order by st.name, ws.step_order
    """)).mappings().all()

    outbound = [r for r in rows if r["service"] == "uchoice_outbound_request"]
    completion = [r for r in rows if r["service"] == "confirm_outbound_completion"]

    # request-time workflow now includes the instruction PDF step
    step_types = [r["step_type"] for r in outbound]
    assert "generate_pdf_stub" in step_types
    pdf_step = next(r for r in outbound if r["step_type"] == "generate_pdf_stub")
    assert pdf_step["config"]["doc_type"] == "outbound_instruction"
    # must run before reply_wechat (post-commit phase, but still before the
    # customer sees the final confirmation reply)
    reply_step = next(r for r in outbound if r["step_type"] == "reply_wechat")
    assert pdf_step["step_order"] < reply_step["step_order"]

    # completion workflow no longer generates the instruction PDF
    assert "generate_pdf_stub" not in [r["step_type"] for r in completion]


def _make_log(db, group_id, sku_lines, destination_address_id, created_at=None):
    from core import request_logger
    service_type_id = db.execute(text(
        "select service_type_id from service_type where name = 'uchoice_outbound_request'"
    )).scalar()
    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=group_id, service_type_id=service_type_id,
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)
    if created_at is not None:
        db.execute(text("update request_log set created_at = :ts where log_id = :lid"),
                   {"ts": created_at, "lid": log.log_id})
        db.commit()
    return log


def test_pdf_uses_request_data_not_fulfillment_lines(db):
    """The new code path must read collected_fields, never context['result']['fulfillment_lines']."""
    from core import access_control
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    address_id = db.execute(text("select address_id from uchoice_address limit 1")).scalar()

    log = _make_log(db, access.group_id, None, None)

    context = {
        "collected_fields": {
            "warehouse_code": WH,
            "destination_address_id": str(address_id),
            "sku_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 2}],
        },
        "serial_number": log.serial_number,
        "request_log_id": str(log.log_id),
        # deliberately no "_uchoice_target" / "result" completion-time keys --
        # if the handler needed those, this would crash or return nothing
    }

    result = GeneratePdfStubHandler().handle(context, {"doc_type": "outbound_instruction"}, db)
    assert result["pdf_status"] == "ready"
    assert result["pdf_url"]


def test_pdf_date_is_stable_across_retries_not_wall_clock(db, monkeypatch):
    """Two calls for the same request, simulating a retry, must not depend on datetime.now()."""
    from core import access_control
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    address_id = db.execute(text("select address_id from uchoice_address limit 1")).scalar()

    fixed_past_date = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    log = _make_log(db, access.group_id, None, None, created_at=fixed_past_date)

    context = {
        "collected_fields": {
            "warehouse_code": WH,
            "destination_address_id": str(address_id),
            "sku_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 2}],
        },
        "serial_number": log.serial_number,
        "request_log_id": str(log.log_id),
    }

    # Spy on the actual date passed into the PDF builder -- this is the
    # thing that must be stable across retries, not raw PDF bytes (which
    # ReportLab stamps with its own non-deterministic internal metadata
    # regardless of what content is passed in, confirmed by direct testing).
    seen_dates = []
    import core.uchoice_delivery_order as delivery_order_module
    original_build = delivery_order_module.build_delivery_order_pdf

    def spy_build(*, delivery_date, **kwargs):
        seen_dates.append(delivery_date)
        return original_build(delivery_date=delivery_date, **kwargs)

    monkeypatch.setattr(delivery_order_module, "build_delivery_order_pdf", spy_build)

    result1 = GeneratePdfStubHandler().handle(context, {"doc_type": "outbound_instruction"}, db)
    result2 = GeneratePdfStubHandler().handle(context, {"doc_type": "outbound_instruction"}, db)

    assert result1["pdf_status"] == result2["pdf_status"] == "ready"
    # different tokens/links (create_token mints a fresh one each call) are
    # fine -- that's not a second logical document, just a fresh access link
    assert result1["pdf_url"] != result2["pdf_url"]

    # but the date driving the document's visible content must be identical
    # across both calls, and must be the persisted 2020-01-01 request
    # timestamp -- never "today," regardless of when the retry happens
    assert seen_dates == [fixed_past_date.date(), fixed_past_date.date()]
