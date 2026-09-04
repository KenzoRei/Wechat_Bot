"""
Regression coverage for the confirm/cancel candidate-gating bug
(docs/reviews/active/2026-09-transfer-warning-and-candidate-gating/plan.md,
Fix B).

core.session_manager._build_uchoice_candidates() used to gate whether to
even attempt building the pending-completion / cancelable-request
candidate list on the CALLER's own granted-services dict (by_name). A
role with completion/cancel rights but not the matching creation right
(warehouseman: confirm_inbound_completion + confirm_outbound_completion,
but never uchoice_inbound_request/uchoice_outbound_request, by design --
warehousemen complete requests, they don't create them) silently saw an
empty candidate list regardless of what was actually in request_log.
Confirmed live in production: a warehouseman ("Jeff") got "当前没有待处理的
出库申请，无需操作" twice on real days with a genuine in-processing outbound
order assigned to his warehouse.

Fix: the paired creation service's service_type_id is now resolved via a
global service_type catalog lookup (core.session_manager._service_type_id_by_name),
independent of both the caller's own grants and the group's current
service enablement.

Real Postgres DB. Fail-closed, exact-row cleanup; never bulk-deletes by
the shared test identity; never mutates the shared fixture group's own
configuration (grants, group_service rows).
"""
import uuid

import pytest
from sqlalchemy import text

from database import SessionLocal
from models.group import GroupConfig
from models.role import Role
from models.kefu import KefuStaff
from models.service import ServiceType
from core import access_control, session_manager

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _real_role_id(db, name: str) -> str:
    role = db.query(Role).filter_by(name=name).one()
    return role.role_id


def _service_type_id(db, name: str) -> str:
    st = db.query(ServiceType).filter_by(name=name).one()
    return st.service_type_id


def _make_staff(db, group_id, role_id, warehouse_codes=None):
    staff = KefuStaff(
        open_kfid=f"kf-gating-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-gating-{uuid.uuid4().hex[:8]}",
        group_id=group_id, role_id=role_id, warehouse_codes=warehouse_codes,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _make_processing_request(db, group_id, service_type_name, warehouse_code, submitted_by_staff_id=None):
    """A real request_log + originating conversation_session, matching the
    shape get_original_fields()/pending_request_candidates() expect."""
    from models.session import ConversationSession
    from models.request_log import RequestLog

    session = ConversationSession(
        group_id=group_id, status="completed", source_channel="kefu",
        collected_fields={
            "warehouse_code": warehouse_code,
            "sku_lines": [{"sku_code": "TEST-GATING-SKU", "boxes_per_pallet": 10, "pallet_count": 1}],
        },
    )
    db.add(session)
    db.flush()
    log = RequestLog(
        group_id=group_id, status="processing", raw_message="test",
        source_channel="kefu", origin_session_id=session.session_id,
        service_type_id=_service_type_id(db, service_type_name),
        submitted_by_staff_id=submitted_by_staff_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return session, log


def _cleanup_request(db, session_id, log_id):
    db.execute(text("delete from request_log where log_id = :lid"), {"lid": log_id})
    db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": session_id})
    db.commit()


def _cleanup_staff(db, staff_id):
    db.execute(text("delete from kefu_staff where staff_id = :sid"), {"sid": staff_id})
    db.commit()


def test_warehouseman_sees_real_processing_outbound_request_as_candidate():
    """Direct regression test for the reported bug: a pure warehouseman
    (no uchoice_outbound_request grant) must see a genuine 'processing'
    outbound request in their assigned warehouse."""
    db = SessionLocal()
    staff = session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        role_id = _real_role_id(db, "warehouseman")
        staff = _make_staff(db, group_id, role_id, warehouse_codes=["JFK"])

        session, log = _make_processing_request(db, group_id, "uchoice_outbound_request", "JFK")
        session_id, log_id = session.session_id, log.log_id

        access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
        candidates = session_manager._build_uchoice_candidates(db, access, session=None)

        serials = {c["serial_number"] for c in candidates.get("pending_outbound_requests", [])}
        assert log.serial_number in serials
    finally:
        if log_id:
            _cleanup_request(db, session_id, log_id)
        if staff:
            _cleanup_staff(db, staff.staff_id)
        db.close()


def test_warehouseman_sees_real_processing_inbound_request_as_candidate():
    db = SessionLocal()
    staff = session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        role_id = _real_role_id(db, "warehouseman")
        staff = _make_staff(db, group_id, role_id, warehouse_codes=["JFK"])

        session, log = _make_processing_request(db, group_id, "uchoice_inbound_request", "JFK")
        session_id, log_id = session.session_id, log.log_id

        access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
        candidates = session_manager._build_uchoice_candidates(db, access, session=None)

        serials = {c["serial_number"] for c in candidates.get("pending_inbound_requests", [])}
        assert log.serial_number in serials
    finally:
        if log_id:
            _cleanup_request(db, session_id, log_id)
        if staff:
            _cleanup_staff(db, staff.staff_id)
        db.close()


def test_role_without_completion_grant_still_sees_no_candidates():
    """A role that genuinely cannot invoke confirm_outbound_completion at
    all must still see nothing -- the "X" in names authorization gate
    itself must keep working; this fix must not accidentally expose
    candidates to an unauthorized role."""
    db = SessionLocal()
    staff = session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        # accountant has neither confirm_outbound_completion nor
        # uchoice_outbound_request granted.
        role_id = _real_role_id(db, "accountant")
        staff = _make_staff(db, group_id, role_id)

        session, log = _make_processing_request(db, group_id, "uchoice_outbound_request", "JFK")
        session_id, log_id = session.session_id, log.log_id

        access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
        candidates = session_manager._build_uchoice_candidates(db, access, session=None)

        assert "pending_outbound_requests" not in candidates
    finally:
        if log_id:
            _cleanup_request(db, session_id, log_id)
        if staff:
            _cleanup_staff(db, staff.staff_id)
        db.close()


@pytest.mark.parametrize(
    "cancel_service_name,creation_service_name,candidate_key",
    [
        ("cancel_outbound_request", "uchoice_outbound_request", "cancelable_outbound_requests"),
        ("cancel_inbound_request", "uchoice_inbound_request", "cancelable_inbound_requests"),
    ],
)
def test_synthetic_cancel_only_role_sees_cancelable_candidates(
    cancel_service_name, creation_service_name, candidate_key
):
    """cancel_outbound_request/cancel_inbound_request's version of the same
    defect is currently latent (every real role granted either one also
    holds the matching creation service), so this constructs the scenario
    with a fully test-owned role -- never mutating grants on any real,
    shared role. Parameterized across both directions (Codex review, round
    3) since core/session_manager.py has separate branches and separate
    catalog lookups for cancel_inbound_request and cancel_outbound_request
    -- covering only one leaves the other's branch unprotected."""
    db = SessionLocal()
    role_id = staff = session_id = log_id = None
    try:
        group_id = _real_group_id(db)
        role_name = f"t_cnl_{uuid.uuid4().hex[:8]}"
        role = Role(name=role_name, description=f"test-owned, {cancel_service_name} only")
        db.add(role)
        db.commit()
        db.refresh(role)
        role_id = role.role_id

        from models.group import GroupServiceRole
        db.add(GroupServiceRole(
            group_id=group_id, role_id=role_id,
            service_type_id=_service_type_id(db, cancel_service_name),
            created_by="test_completion_cancel_candidate_gating",
        ))
        db.commit()

        staff = _make_staff(db, group_id, role_id)
        session, log = _make_processing_request(
            db, group_id, creation_service_name, "JFK",
            submitted_by_staff_id=staff.staff_id,
        )
        session_id, log_id = session.session_id, log.log_id

        access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
        candidates = session_manager._build_uchoice_candidates(db, access, session=None)

        serials = {c["serial_number"] for c in candidates.get(candidate_key, [])}
        assert log.serial_number in serials
    finally:
        db.rollback()
        if log_id:
            _cleanup_request(db, session_id, log_id)
        if staff:
            _cleanup_staff(db, staff.staff_id)
        if role_id:
            db.execute(text("delete from group_service_role where role_id = :rid"), {"rid": role_id})
            db.execute(text("delete from role where role_id = :rid"), {"rid": role_id})
            db.commit()
        db.close()


def test_disabling_creation_service_for_group_does_not_hide_existing_processing_request():
    """Codex-driven redesign of this test: proves catalog-independence
    without ever deleting a real group_service row (group_service_role's
    FK to group_service is ON DELETE CASCADE -- deleting a real group's
    uchoice_outbound_request group_service row would silently wipe every
    role's grant for it in that group, with no way to restore them from
    just re-inserting group_service). Builds a fully synthetic group that
    is granted confirm_outbound_completion but NEVER has a group_service
    row for uchoice_outbound_request at all -- functionally equivalent to
    "an admin removed the creation service", reached with zero destructive
    steps and zero risk to the shared fixture group."""
    db = SessionLocal()
    group_id = role_id = staff = session_id = log_id = None
    try:
        from models.group import GroupConfig as GC, GroupService, GroupServiceRole
        from models.workflow import Workflow

        group = GC(
            wechat_group_id=f"test-gating-{uuid.uuid4().hex[:12]}",
            description="test-owned synthetic group for candidate-gating test",
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        group_id = group.group_id

        completion_service_id = _service_type_id(db, "confirm_outbound_completion")
        completion_workflow_id = db.query(Workflow.workflow_id).join(
            GroupService, GroupService.workflow_id == Workflow.workflow_id
        ).filter(GroupService.service_type_id == completion_service_id).first()[0]

        db.add(GroupService(
            group_id=group_id, service_type_id=completion_service_id,
            workflow_id=completion_workflow_id, config={},
        ))
        db.commit()
        # Deliberately NO GroupService row for uchoice_outbound_request in
        # this synthetic group.

        role_name = f"t_gate_{uuid.uuid4().hex[:8]}"
        role = Role(name=role_name, description="test-owned")
        db.add(role)
        db.commit()
        db.refresh(role)
        role_id = role.role_id

        db.add(GroupServiceRole(
            group_id=group_id, role_id=role_id, service_type_id=completion_service_id,
            created_by="test_completion_cancel_candidate_gating",
        ))
        db.commit()

        staff = _make_staff(db, group_id, role_id, warehouse_codes=["JFK"])
        session, log = _make_processing_request(db, group_id, "uchoice_outbound_request", "JFK")
        session_id, log_id = session.session_id, log.log_id

        access = access_control.check_kefu_access(db, staff.open_kfid, staff.external_userid)
        candidates = session_manager._build_uchoice_candidates(db, access, session=None)

        serials = {c["serial_number"] for c in candidates.get("pending_outbound_requests", [])}
        assert log.serial_number in serials, (
            "a synthetic group with confirm_outbound_completion granted but NO "
            "group_service row for the creation service must still surface an "
            "existing processing request -- group-level enablement of the "
            "historical creation service must have zero effect on already "
            "in-flight work"
        )
    finally:
        db.rollback()
        if log_id:
            _cleanup_request(db, session_id, log_id)
        if staff:
            _cleanup_staff(db, staff.staff_id)
        if role_id:
            db.execute(text("delete from group_service_role where role_id = :rid"), {"rid": role_id})
            db.execute(text("delete from role where role_id = :rid"), {"rid": role_id})
            db.commit()
        if group_id:
            db.execute(text("delete from group_service where group_id = :gid"), {"gid": group_id})
            db.execute(text("delete from group_config where group_id = :gid"), {"gid": group_id})
            db.commit()
        db.close()


def test_warehouseman_completes_real_outbound_request_via_kefu_turn_end_to_end(monkeypatch):
    """
    Full-pipeline reproduction of the live incident: a warehouseman ("Jeff")
    asks Kefu to confirm an outbound request WITHOUT stating its serial
    number -- the AI extracts no reference_serial, exactly as it did live --
    forcing resolution through _resolve_reference_serial's candidate-list
    lookup (core/kefu_turn_apply.py), which is fed by the now-fixed
    core.session_manager._build_uchoice_candidates(). Before the fix, this
    always rejected with "当前没有待处理的出库申请，无需操作。" for a pure
    warehouseman regardless of real request_log content; now it must reach
    the fulfillment/confirmation step exactly as it does for an admin in
    tests/kefu_integration/test_kefu_outbound_completion.py.

    Runs in a fully synthetic group (Codex review, round 3): the single-
    candidate auto-resolution this test exercises depends on there being
    EXACTLY one eligible pending_outbound_requests candidate.
    pending_request_candidates scopes strictly by RequestLog.group_id, so
    using the shared fixture group would make this test's outcome depend on
    however many genuine processing JFK outbound requests happen to exist
    there at run time -- non-deterministic, and liable to flip into the
    ambiguity path instead of auto-resolving. A synthetic group makes
    "exactly one" a fact of the test's own setup, not an assumption about
    live data.
    """
    from datetime import datetime, timedelta, timezone
    from ai.base import AIResponse
    from core.kefu_contracts import KefuIdentity
    from models.group import GroupConfig as GC, GroupService, GroupServiceRole
    from models.request_log import RequestLog
    from models.session import ConversationSession
    from models.workflow import Workflow
    import core.kefu_case_adapter as adapter

    db = SessionLocal()
    group_id = role_grant_created = staff_id = original_session_id = log_id = None
    sku = f"kg{uuid.uuid4().hex[:8]}"
    try:
        role_id = _real_role_id(db, "warehouseman")
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()
        completion_service_id = _service_type_id(db, "confirm_outbound_completion")

        group = GC(
            wechat_group_id=f"test-gating-e2e-{uuid.uuid4().hex[:12]}",
            description="test-owned synthetic group for candidate-gating e2e test",
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        group_id = group.group_id

        completion_workflow_id = db.query(Workflow.workflow_id).join(
            GroupService, GroupService.workflow_id == Workflow.workflow_id
        ).filter(GroupService.service_type_id == completion_service_id).first()[0]
        db.add(GroupService(
            group_id=group_id, service_type_id=completion_service_id,
            workflow_id=completion_workflow_id, config={},
        ))
        db.commit()
        # Deliberately NO GroupService row for uchoice_outbound_request --
        # this test only exercises completion, mirroring the real
        # warehouseman grant shape (see module docstring).

        db.add(GroupServiceRole(
            group_id=group_id, role_id=role_id, service_type_id=completion_service_id,
            created_by="test_completion_cancel_candidate_gating",
        ))
        db.commit()
        role_grant_created = True

        staff = KefuStaff(
            open_kfid=f"kf-gating-e2e-{uuid.uuid4().hex[:8]}",
            external_userid=f"staff-gating-e2e-{uuid.uuid4().hex[:8]}",
            group_id=group_id, role_id=role_id, warehouse_codes=["JFK"],
        )
        db.add(staff)
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:sku,'Fix B e2e test')"), {"sku": sku})
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values ('JFK',:sku,40,2)"
        ), {"sku": sku})
        db.flush()
        original = ConversationSession(
            group_id=group_id, service_type_id=outbound_type.service_type_id, status="completed",
            conversation_history=[],
            collected_fields={
                "warehouse_code": "JFK",
                "sku_lines": [{"sku_code": sku, "boxes_per_pallet": 40, "pallet_count": 2}],
            },
            source_channel="kefu", opened_by_staff_id=staff.staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(original)
        db.flush()
        log = RequestLog(
            group_id=group_id, service_type_id=outbound_type.service_type_id, status="processing",
            raw_message="original outbound", source_channel="kefu",
            submitted_by_staff_id=staff.staff_id, origin_session_id=original.session_id,
        )
        db.add(log)
        db.flush()
        original.request_log_id = log.log_id
        db.commit()
        db.refresh(staff)
        db.refresh(log)
        staff_id, original_session_id, log_id = staff.staff_id, original.session_id, log.log_id
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        db.close()

        # No reference_serial extracted at all -- must resolve via the
        # single-eligible-candidate auto-fill in _resolve_reference_serial.
        responses = iter([
            AIResponse(
                intent="new_request", service_type_name="confirm_outbound_completion",
                extracted_fields={}, all_fields_collected=True, reply="正在确认",
            ),
            AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=True, reply="确认"),
        ])
        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(responses))
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        pending = processor(
            identity=identity, message_content="确认出库",
            message_meta={"msgid": f"gating-e2e-open-{uuid.uuid4().hex}"}, case_number_hint=None,
        )
        assert "无需操作" not in pending.reply_text, (
            f"warehouseman must see the real pending request, not the empty-candidate "
            f"rejection -- got: {pending.reply_text!r}"
        )
        assert "确认以下信息" in pending.reply_text

        completed = processor(
            identity=identity, message_content="确认",
            message_meta={"msgid": f"gating-e2e-confirm-{uuid.uuid4().hex}"}, case_number_hint=pending.case_number,
        )
        assert completed.reply_text

        db = SessionLocal()
        assert db.execute(text("select status from request_log where log_id=:id"), {"id": log_id}).scalar_one() == "success"
    finally:
        try:
            db.rollback()
        except Exception:
            db = SessionLocal()
        if staff_id:
            sessions = db.execute(text("select session_id from conversation_session where opened_by_staff_id=:staff"), {"staff": staff_id}).scalars().all()
            db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {"staff": staff_id, "sessions": sessions})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from request_log where submitted_by_staff_id=:staff or log_id=:log"), {"staff": staff_id, "log": log_id})
            db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
        if role_grant_created:
            db.execute(text(
                "delete from group_service_role where group_id = :gid and role_id = :rid"
            ), {"gid": group_id, "rid": role_id})
        if group_id:
            db.execute(text("delete from group_service where group_id = :gid"), {"gid": group_id})
            db.execute(text("delete from group_config where group_id = :gid"), {"gid": group_id})
        db.execute(text("delete from uchoice_storage_txn where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_storage where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_sku where sku_code=:sku"), {"sku": sku})
        db.commit()
        db.close()
