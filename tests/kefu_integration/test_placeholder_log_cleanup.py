"""
Regression coverage for the targets_existing_request placeholder-log leak
(docs/reviews/active/2026-09-admin-panel-expansion/plan.md, Fix 1).

A fresh turn for a targets_existing_request service (cancel_outbound_request,
confirm_outbound_completion, etc.) unconditionally creates a placeholder
ConversationSession + RequestLog pair before it's known whether there's
actually a real target. Four rejection paths -- zero eligible candidates,
an explicitly-referenced serial that doesn't exist, one that exists but
isn't 'processing', and (implicitly, same code path as the second) a
missing reference after all -- used to leave that placeholder behind
forever. Confirmed live in production: REQ-20260903-000022,
raw_message='货送到了', status='pending' since 2026-09-03, never resolved.

cancel_outbound_request + a freshly-created 'customer'-role staff member is
used throughout: cancelable_request_candidates() scopes non-admin
candidates to requests THAT STAFF MEMBER submitted (core/uchoice_context.py),
so a brand-new staff row with zero prior submissions deterministically has
zero eligible candidates regardless of what else exists in the shared
group -- no reliance on ambient database state.

Real Postgres DB. Fail-closed, exact-row cleanup; never bulk-deletes by
the shared test identity.
"""
import uuid

import pytest
from sqlalchemy import text

from ai.base import AIResponse
from database import SessionLocal
import models.request_log  # noqa: F401 -- registers RequestLog for FK resolution
import core.kefu_case_adapter as adapter
from core.kefu_contracts import KefuIdentity

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    from models.group import GroupConfig
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _real_role_id(db, name="customer") -> str:
    from models.role import Role
    role = db.query(Role).filter_by(name=name).first()
    assert role is not None, f"'{name}' role not found -- seed data missing"
    return role.role_id


def _make_staff_row(db, group_id, role_id):
    from models.kefu import KefuStaff
    row = KefuStaff(
        open_kfid=f"kf-leaktest-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-leaktest-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=role_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _cleanup_staff_rows(staff_ids, msgid_prefixes, extra_log_ids=()):
    db2 = SessionLocal()
    try:
        for sid in staff_ids:
            db2.execute(text("delete from case_turn where acting_staff_id = :sid"), {"sid": sid})
            db2.execute(text("delete from kefu_outbound_delivery where recipient_staff_id = :sid"), {"sid": sid})
        for pfx in msgid_prefixes:
            db2.execute(text("delete from case_execution where execution_key like :pfx"), {"pfx": f"kefu:{pfx}%"})
        for sid in staff_ids:
            session_ids = set(db2.execute(text(
                "select origin_session_id from request_log where submitted_by_staff_id = :sid"
            ), {"sid": sid}).scalars().all())
            # A session whose placeholder request_log was deleted by the
            # leak fix under test is no longer reachable via
            # request_log.origin_session_id -- opened_by_staff_id still
            # finds it directly.
            session_ids |= set(db2.execute(text(
                "select session_id from conversation_session where opened_by_staff_id = :sid"
            ), {"sid": sid}).scalars().all())
            db2.execute(text("delete from request_log where submitted_by_staff_id = :sid"), {"sid": sid})
            for s in session_ids:
                if s is not None:
                    db2.execute(text("delete from conversation_session where session_id = :s"), {"s": s})
            db2.execute(text("delete from kefu_staff_case_context where staff_id = :sid"), {"sid": sid})
            db2.execute(text("delete from kefu_staff where staff_id = :sid"), {"sid": sid})
        for lid in extra_log_ids:
            db2.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
        db2.commit()
    finally:
        db2.close()


@pytest.fixture
def staff():
    row = None
    db = SessionLocal()
    try:
        group_id = _real_group_id(db)
        role_id = _real_role_id(db, "customer")
        row = _make_staff_row(db, group_id, role_id)
        yield row
    finally:
        db.close()
        if row is not None:
            _cleanup_staff_rows([row.staff_id], ["leak-"])


def _canned_cancel_response(reference_serial: str | None = None):
    extracted = {"reference_serial": reference_serial} if reference_serial else {}
    return AIResponse(
        intent="new_request",
        reply="",
        extracted_fields=extracted,
        all_fields_collected=True,
        service_type_name="cancel_outbound_request",
    )


def _run_turn(monkeypatch, staff, msgid, reference_serial=None):
    def fake_process(context):
        return _canned_cancel_response(reference_serial)

    monkeypatch.setattr(adapter._ai_chain, "process", fake_process)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    identity = KefuIdentity(open_kfid=staff.open_kfid, external_userid=staff.external_userid)
    return processor(identity=identity, message_content="取消出库", message_meta={"msgid": msgid}, case_number_hint=None)


def _assert_no_placeholder_and_session_terminal(msgid, staff_id):
    db = SessionLocal()
    try:
        from models.request_log import RequestLog
        from models.session import ConversationSession
        from models.kefu import KefuStaffCaseContext

        assert db.query(RequestLog).filter_by(wechat_msg_id=msgid).count() == 0, \
            "the turn-local placeholder request_log must not survive a terminal rejection"

        session = (
            db.query(ConversationSession)
            .filter(ConversationSession.opened_by_staff_id == staff_id)
            .order_by(ConversationSession.created_at.desc())
            .first()
        )
        assert session is not None, "a session should still exist even though its request_log was discarded"
        assert session.status not in ("active", "pending_confirmation"), \
            f"session must end terminal, not {session.status!r}"
        assert session.request_log_id is None, \
            "session.request_log_id must be cleared in-memory/persisted, not left pointing at a deleted row"

        binding = db.get(KefuStaffCaseContext, staff_id)
        assert binding is None or binding.active_session_id != session.session_id, \
            "staff's active-case binding must not be left pointing at the now-terminal session"
    finally:
        db.close()


def test_zero_eligible_candidates_discards_placeholder(monkeypatch, staff):
    """cancel_outbound_request, no reference given, this staff has never
    submitted anything -- cancelable_request_candidates() is deterministically
    empty for them regardless of ambient shared-group data."""
    msgid = f"leak-zero-{uuid.uuid4().hex[:12]}"
    _run_turn(monkeypatch, staff, msgid)
    _assert_no_placeholder_and_session_terminal(msgid, staff.staff_id)


def test_unknown_serial_discards_placeholder(monkeypatch, staff):
    """An explicit reference_serial that doesn't exist in request_log at all."""
    msgid = f"leak-unknown-{uuid.uuid4().hex[:12]}"
    _run_turn(monkeypatch, staff, msgid, reference_serial="REQ-99999999-999999")
    _assert_no_placeholder_and_session_terminal(msgid, staff.staff_id)


def test_resolve_existing_target_missing_reference_branch_returns_error_not_a_target():
    """
    _resolve_existing_target's "if not reference" branch (line ~309-310)
    is, as far as this codebase's current schema goes, unreachable via any
    real conversational turn: cancel_outbound_request's (and every other
    targets_existing_request service's) input_schema declares
    reference_serial as REQUIRED, and _all_required_fields_present already
    rejects an empty reference_serial -- returning "please provide a
    request number" -- before apply_kefu_turn ever reaches the call site
    that invokes _resolve_existing_target. Confirmed empirically: a fresh
    turn with a single eligible candidate lacking serial_number (so
    _resolve_reference_serial can't auto-fill it) is intercepted by the
    missing-fields check first, never reaching _resolve_existing_target at
    all.

    This is still real defensive code, worth its own contract test in
    isolation -- if a future schema change ever makes reference_serial
    optional, or _resolve_existing_target gains a second caller, this
    branch (and the caller-side placeholder cleanup that already handles
    everything _resolve_existing_target returns as an error) is what
    protects against silently adopting a target with no reference at all.
    """
    from types import SimpleNamespace
    from core.kefu_turn_apply import _resolve_existing_target

    db = SessionLocal()
    try:
        session = SimpleNamespace(collected_fields={})
        target, error = _resolve_existing_target(db, session, owned_log=None)
        assert target is None
        assert error == "未能确定要处理的申请编号，请重新描述或提供申请编号。"
    finally:
        db.close()


def test_wrong_status_serial_discards_placeholder_and_leaves_real_target_untouched(monkeypatch, staff):
    """An explicit reference_serial that exists but isn't 'processing' --
    must not be adopted, and the real (unrelated) row must be left exactly
    as it was."""
    db = SessionLocal()
    real_log_id = None
    try:
        group_id = _real_group_id(db)
        from models.request_log import RequestLog
        real_log = RequestLog(
            group_id=group_id,
            status="success",
            raw_message="unrelated already-completed request",
            source_channel="kefu",
        )
        db.add(real_log)
        db.commit()
        db.refresh(real_log)
        real_log_id = real_log.log_id
        real_serial = real_log.serial_number
    finally:
        db.close()

    msgid = f"leak-wrongstatus-{uuid.uuid4().hex[:12]}"
    try:
        _run_turn(monkeypatch, staff, msgid, reference_serial=real_serial)
        _assert_no_placeholder_and_session_terminal(msgid, staff.staff_id)

        verify_db = SessionLocal()
        try:
            from models.request_log import RequestLog
            untouched = verify_db.get(RequestLog, real_log_id)
            assert untouched is not None, "the real, unrelated request must not have been deleted"
            assert untouched.status == "success", "the real target's own status must be untouched"
        finally:
            verify_db.close()
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.execute(text("delete from request_log where log_id = :lid"), {"lid": real_log_id})
            cleanup_db.commit()
        finally:
            cleanup_db.close()
