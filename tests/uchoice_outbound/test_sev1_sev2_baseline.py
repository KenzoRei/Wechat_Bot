"""
Pre-implementation baseline for Phase 1 (agreed-plan.md): Sev 1 (zero-bucket
None/未知 leak) and Sev 2 (missing sku_code reaches confirmation).

Uses the real dev Postgres DB (not a mock) — Sev 1 specifically depends on
real UchoiceStorage bucket-existence checks that a mock DB can't faithfully
reproduce, and this matches the pattern already proven live earlier this
session (test_full_redesign.py's 9 passing groups). A throwaway warehouse
code (TESTWHX) isolates fixtures from real data; everything is cleaned up
in a finally block.

Run against CURRENT, unmodified core/workflow_engine.py + core/confirmation.py
+ core/pre_confirm_validators.py. Sev 1/Sev 2 cases are marked xfail(strict=True)
-- they document real bugs and must fail until Phase 1 lands. If one starts
passing unexpectedly, pytest fails loudly rather than silently losing coverage.
"""
import uuid
import pytest
from sqlalchemy import text

from database import SessionLocal
from core import access_control, session_manager, workflow_engine, confirmation

WH = "TESTWHX"
OPENID = "transworld"  # real, already-onboarded group_member used throughout this project's testing


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# Track exact rows this test module creates -- never bulk-delete/cancel by
# the shared wechat_openid, which would touch other concurrent test runs'
# or real data for this same identity (flagged correctly by Codex's
# cross-review; a fail-closed, exact-row cleanup replaces the earlier
# broad-by-identity approach).
_created_session_ids: list = []
_created_log_ids: list = []


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    for lid in _created_log_ids:
        db.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
    for sid in _created_session_ids:
        db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": sid})
    _created_session_ids.clear()
    _created_log_ids.clear()
    db.execute(text("delete from uchoice_storage_txn where warehouse_code = :wh"), {"wh": WH})
    db.execute(text("delete from uchoice_storage where warehouse_code = :wh"), {"wh": WH})
    db.commit()


WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"  # real test group used throughout this project's sessions


def _group_and_address(db):
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    address_id = db.execute(text("select address_id from uchoice_address limit 1")).scalar()
    return access.group_id, address_id


def _make_session(db, group_id, service_name, collected_fields):
    # This identity can have at most one active/pending session at a time
    # (idx_session_one_active_per_user) -- rather than cancelling any
    # existing one (which could belong to a concurrent run), fail loudly so
    # a stuck prior run is visible instead of silently clobbered.
    existing = db.execute(text(
        "select session_id from conversation_session where wechat_openid = :o "
        "and status in ('active','pending_confirmation')"
    ), {"o": OPENID}).scalar()
    if existing is not None:
        pytest.fail(
            f"wechat_openid={OPENID!r} already has an active session ({existing}) -- "
            "not cancelling it (could belong to a concurrent run); clean it up manually first"
        )

    service_type_id = db.execute(
        text("select service_type_id from service_type where name = :n"), {"n": service_name}
    ).scalar()
    session = session_manager.create_session(
        db, wechat_openid=OPENID, group_id=group_id,
        initial_message="test", service_type_id=service_type_id,
    )
    _created_session_ids.append(session.session_id)
    from core import request_logger
    log = request_logger.create_log(
        db, wechat_openid=OPENID, group_id=group_id, service_type_id=service_type_id,
        raw_message="test", wechat_msg_id=None,
    )
    _created_log_ids.append(log.log_id)
    session.request_log_id = log.log_id
    db.commit()
    session_manager.update_collected_fields(db, session, collected_fields)
    return session


def _context_for(db, group_id, session):
    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    message = {"content": "test", "msg_id": None, "response_url": ""}
    context = session_manager.build_context(db, access, session, message)
    context["request_log_id"] = str(session.request_log_id)
    context["serial_number"] = db.execute(
        text("select serial_number from request_log where log_id = :lid"),
        {"lid": session.request_log_id},
    ).scalar()
    return context


def test_zero_bucket_sku_does_not_reach_confirmation(db):
    group_id, address_id = _group_and_address(db)
    # 's1' with zero uchoice_storage rows in WH -- confirmed no buckets exist for a throwaway warehouse
    session = _make_session(db, group_id, "uchoice_outbound_request", {
        "warehouse_code": WH,
        "destination_address_id": str(address_id),
        "sku_lines": [{"sku_code": "s1", "pallet_count": 1}],
    })
    context = _context_for(db, group_id, session)

    workflow_engine._resolve_outbound_pallet_defaults(context, session, db)
    rejected = workflow_engine._reject_invalid_outbound_stock(context, session, db)

    # fixed behavior: the zero-real-bucket line is caught and the request is
    # rejected outright, before any confirmation is ever built -- no answer
    # the customer gives could make inventory exist.
    assert rejected is True, "the zero-bucket line should have been rejected before confirmation"
    assert session.status == "cancelled"


def test_missing_sku_code_does_not_reach_confirmation(db):
    group_id, address_id = _group_and_address(db)
    session = _make_session(db, group_id, "uchoice_outbound_request", {
        "warehouse_code": WH,
        "destination_address_id": str(address_id),
        "sku_lines": [{"box_count": 30}],  # no sku_code at all
    })
    context = _context_for(db, group_id, session)

    # current (buggy) behavior: _outbound_required_fields_present only checks
    # sku_lines is non-empty, so this is treated as "collected" and reaches
    # confirmation with sku label "?"
    service = {"name": "uchoice_outbound_request"}
    force_complete = workflow_engine._outbound_required_fields_present(service, session)
    assert force_complete is False, "a line with no sku_code must not count as a required field being satisfied"


def test_real_stock_sku_reaches_confirmation_correctly(db):
    """Control case: a SKU with genuine stock must still work exactly as before."""
    group_id, address_id = _group_and_address(db)
    db.execute(text(
        "insert into uchoice_storage (warehouse_code, sku_code, boxes_per_pallet, pallet_count) "
        "values (:wh, 's4', 64, 5)"
    ), {"wh": WH})
    db.commit()

    session = _make_session(db, group_id, "uchoice_outbound_request", {
        "warehouse_code": WH,
        "destination_address_id": str(address_id),
        "sku_lines": [{"sku_code": "s4", "pallet_count": 1}],
    })
    context = _context_for(db, group_id, session)

    clarification = workflow_engine._resolve_outbound_pallet_defaults(context, session, db)
    assert clarification is None
    rejected = workflow_engine._reject_invalid_outbound_stock(context, session, db)
    assert rejected is False

    db.refresh(session)
    assert session.collected_fields["sku_lines"][0]["boxes_per_pallet"] == 64
