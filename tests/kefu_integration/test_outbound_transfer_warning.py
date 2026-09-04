"""
Coverage for the internal-transfer warning shown when an outbound
request's destination resolves to one of the company's own warehouses
(UchoiceAddress.destination_warehouse_code) -- previously untested
entirely (verified: destination_warehouse_code only appears in
tests/kefu_integration/test_kefu_box_fulfillment.py, which exercises the
storage-mutation mechanics, not confirmation-message text).

Two builders render two different, deliberately different-worded
messages for the same underlying fact:
- core.confirmation._outbound_sections_builder (order creation): future
  tense -- confirming only advances the request to 'processing', no
  inventory moves yet.
- core.confirmation._outbound_completion_sections_builder (warehouse
  completion): present tense -- confirming here is the actual
  inventory-moving step.

Real Postgres DB. Fail-closed, exact-row cleanup; never bulk-deletes by
the shared test identity.
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from models.group import GroupConfig
from models.uchoice import UchoiceAddress
from core.confirmation import _outbound_sections_builder, _outbound_completion_sections_builder


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id="wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A").first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


@pytest.fixture
def internal_address():
    db = SessionLocal()
    addr = UchoiceAddress(
        company_name="Test Internal Warehouse", charge_type="truck_transfer",
        addr="test internal transfer address", warehouse_code="JFK",
        created_by="test_transfer_warning", destination_warehouse_code="NJ",
    )
    db.add(addr)
    db.commit()
    db.refresh(addr)
    yield addr
    db.execute(text("delete from uchoice_address where address_id = :aid"), {"aid": addr.address_id})
    db.commit()
    db.close()


@pytest.fixture
def external_address():
    db = SessionLocal()
    addr = UchoiceAddress(
        company_name="Test External Customer", charge_type="delivery",
        addr="test external customer address", warehouse_code="JFK",
        created_by="test_transfer_warning", destination_warehouse_code=None,
    )
    db.add(addr)
    db.commit()
    db.refresh(addr)
    yield addr
    db.execute(text("delete from uchoice_address where address_id = :aid"), {"aid": addr.address_id})
    db.commit()
    db.close()


def test_creation_confirmation_shows_future_tense_warning_for_internal_address(internal_address):
    db = SessionLocal()
    try:
        collected_fields = {
            "warehouse_code": "JFK",
            "sku_lines": [{"sku_code": "TEST-SKU-1", "boxes_per_pallet": 10, "pallet_count": 1}],
            "destination_address_id": str(internal_address.address_id),
        }
        sections = _outbound_sections_builder(collected_fields, db)
        text_out = "\n".join(item for s in sections for item in s["items"])
        assert "仓库确认出库完成后，将同时增加 NJ 仓对应库存" in text_out
        # Must NOT use the completion builder's present-tense wording.
        assert "确认后将同时增加 NJ 仓对应库存" not in text_out.replace(
            "仓库确认出库完成后，将同时增加 NJ 仓对应库存", ""
        )
    finally:
        db.close()


def test_creation_confirmation_shows_no_warning_for_external_address(external_address):
    db = SessionLocal()
    try:
        collected_fields = {
            "warehouse_code": "JFK",
            "sku_lines": [{"sku_code": "TEST-SKU-1", "boxes_per_pallet": 10, "pallet_count": 1}],
            "destination_address_id": str(external_address.address_id),
        }
        sections = _outbound_sections_builder(collected_fields, db)
        text_out = "\n".join(item for s in sections for item in s["items"])
        assert "内部调仓" not in text_out
    finally:
        db.close()


def _make_completion_target(db, group_id, address_id):
    from models.session import ConversationSession
    from models.request_log import RequestLog

    session = ConversationSession(
        group_id=group_id, status="active", source_channel="kefu",
        collected_fields={
            "warehouse_code": "JFK",
            "sku_lines": [{"sku_code": "TEST-SKU-1", "boxes_per_pallet": 10, "pallet_count": 1}],
            "destination_address_id": str(address_id),
        },
    )
    db.add(session)
    db.flush()
    log = RequestLog(
        group_id=group_id, status="processing", raw_message="test",
        source_channel="kefu", origin_session_id=session.session_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return session, log


def test_completion_confirmation_still_shows_its_own_present_tense_warning(internal_address):
    db = SessionLocal()
    session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        session, log = _make_completion_target(db, group_id, internal_address.address_id)
        session_id, log_id = session.session_id, log.log_id

        sections = _outbound_completion_sections_builder({"reference_serial": log.serial_number}, db)
        text_out = "\n".join(item for s in sections for item in s["items"])
        assert "⚠️ 此为内部调仓：确认后将同时增加 NJ 仓对应库存" in text_out
    finally:
        if log_id:
            db.execute(text("delete from request_log where log_id = :lid"), {"lid": log_id})
        if session_id:
            db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session_id})
        db.commit()
        db.close()


def test_completion_confirmation_shows_no_warning_for_external_address(external_address):
    db = SessionLocal()
    session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        session, log = _make_completion_target(db, group_id, external_address.address_id)
        session_id, log_id = session.session_id, log.log_id

        sections = _outbound_completion_sections_builder({"reference_serial": log.serial_number}, db)
        text_out = "\n".join(item for s in sections for item in s["items"])
        assert "内部调仓" not in text_out
    finally:
        if log_id:
            db.execute(text("delete from request_log where log_id = :lid"), {"lid": log_id})
        if session_id:
            db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session_id})
        db.commit()
        db.close()
