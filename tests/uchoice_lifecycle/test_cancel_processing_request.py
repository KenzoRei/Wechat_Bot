"""
core.pre_confirm_validators._valid_cancel_target_and_owner: status/direction/
group/ownership checks for cancel_inbound_request/cancel_outbound_request.
Mock-based (monkeypatches core.uchoice_context.resolve_completion_target),
matching this project's offline-test pattern (e.g.
tests/uchoice_self_registration/test_kefu_case_session_resolution.py) --
the row-lock/concurrency behavior itself needs a real Postgres session and
is covered separately by the postgres-marked tests in this module.
"""
from types import SimpleNamespace

import pytest

from core import pre_confirm_validators


def _target(*, status="processing", group_id="g1", source_channel="smart_robot",
            wechat_openid="cust-1", submitted_by_staff_id=None, serial_number="REQ-1",
            service_type_id="uchoice_inbound_request"):
    return SimpleNamespace(
        status=status, group_id=group_id, source_channel=source_channel,
        wechat_openid=wechat_openid, submitted_by_staff_id=submitted_by_staff_id,
        serial_number=serial_number, service_type_id=service_type_id,
    )


def _context(*, role="customer", group_id="g1", source_channel="smart_robot",
             wechat_openid="cust-1", submitted_by_staff_id=None):
    return {
        "role": role, "group_id": group_id, "source_channel": source_channel,
        "wechat_openid": wechat_openid, "submitted_by_staff_id": submitted_by_staff_id,
    }


class _FakeServiceTypeDB:
    """
    Fake db supporting only what _valid_cancel_target_and_owner's direction
    check needs: db.query(ServiceType).filter_by(service_type_id=X).first()
    -- echoes back a fake ServiceType whose .name equals whatever
    service_type_id was looked up (the _target() helper above sets
    service_type_id directly to the real service name, e.g.
    "uchoice_inbound_request", so no separate id->name mapping is needed).
    """

    class _Query:
        def __init__(self, service_type_id):
            self._service_type_id = service_type_id

        def filter_by(self, **kwargs):
            self._service_type_id = kwargs.get("service_type_id", self._service_type_id)
            return self

        def first(self):
            if self._service_type_id is None:
                return None
            return SimpleNamespace(name=self._service_type_id)

    def query(self, model):
        return self._Query(None)


def _patch_target(monkeypatch, target):
    monkeypatch.setattr(
        "core.uchoice_context.resolve_completion_target",
        lambda db, reference_serial: (target, {}),
    )


def test_owner_can_cancel_own_smart_robot_request(monkeypatch):
    target = _target(source_channel="smart_robot", wechat_openid="cust-1")
    _patch_target(monkeypatch, target)
    context = _context(role="customer", source_channel="smart_robot", wechat_openid="cust-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is None


def test_different_customer_is_denied(monkeypatch):
    target = _target(source_channel="smart_robot", wechat_openid="cust-1")
    _patch_target(monkeypatch, target)
    context = _context(role="customer", source_channel="smart_robot", wechat_openid="cust-2")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


def test_admin_can_cancel_anyone_in_same_group(monkeypatch):
    target = _target(source_channel="smart_robot", wechat_openid="cust-1", group_id="g1")
    _patch_target(monkeypatch, target)
    context = _context(role="admin", group_id="g1", wechat_openid="admin-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is None


def test_admin_cannot_cancel_across_groups(monkeypatch):
    target = _target(source_channel="smart_robot", wechat_openid="cust-1", group_id="g1")
    _patch_target(monkeypatch, target)
    context = _context(role="admin", group_id="g2", wechat_openid="admin-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


def test_pending_request_is_rejected_as_not_cancellable(monkeypatch):
    target = _target(status="pending", source_channel="smart_robot", wechat_openid="cust-1")
    _patch_target(monkeypatch, target)
    context = _context(role="customer", wechat_openid="cust-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


def test_already_cancelled_request_is_rejected(monkeypatch):
    target = _target(status="cancelled", source_channel="smart_robot", wechat_openid="cust-1")
    _patch_target(monkeypatch, target)
    context = _context(role="customer", wechat_openid="cust-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


def test_unknown_serial_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "core.uchoice_context.resolve_completion_target",
        lambda db, reference_serial: (None, {}),
    )
    context = _context(role="customer", wechat_openid="cust-1")
    error = pre_confirm_validators.run(
        "cancel_inbound_request", context, {"reference_serial": "REQ-404"}, db=None,
    )
    assert error is not None


def test_kefu_owner_can_cancel_own_request(monkeypatch):
    target = _target(
        source_channel="kefu", wechat_openid=None, submitted_by_staff_id="staff-1",
        service_type_id="uchoice_outbound_request",
    )
    _patch_target(monkeypatch, target)
    context = _context(role="customer", source_channel="kefu", wechat_openid=None, submitted_by_staff_id="staff-1")
    error = pre_confirm_validators.run(
        "cancel_outbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is None


def test_kefu_staff_id_never_satisfies_smart_robot_ownership_check(monkeypatch):
    """
    Ownership matching must be channel-aware and fail-closed on
    inconsistent provenance -- a Kefu-originated request's
    submitted_by_staff_id must never be satisfied by a Smart Bot caller's
    wechat_openid, or vice versa, even if the raw string values happened to
    coincide.
    """
    target = _target(
        source_channel="kefu", wechat_openid=None, submitted_by_staff_id="staff-1",
        service_type_id="uchoice_outbound_request",
    )
    _patch_target(monkeypatch, target)
    # Caller is on the Smart Bot channel, with a wechat_openid that happens
    # to equal the string "staff-1" -- must still be denied, since the
    # target's provenance is Kefu, not Smart Bot.
    context = _context(role="customer", source_channel="smart_robot", wechat_openid="staff-1")
    error = pre_confirm_validators.run(
        "cancel_outbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


def test_smart_robot_owner_never_satisfies_kefu_ownership_check(monkeypatch):
    target = _target(
        source_channel="smart_robot", wechat_openid="cust-1", submitted_by_staff_id=None,
        service_type_id="uchoice_outbound_request",
    )
    _patch_target(monkeypatch, target)
    context = _context(role="customer", source_channel="kefu", wechat_openid=None, submitted_by_staff_id="cust-1")
    error = pre_confirm_validators.run(
        "cancel_outbound_request", context, {"reference_serial": "REQ-1"}, db=_FakeServiceTypeDB(),
    )
    assert error is not None


# ── shared warehouse-scope enforcement (core.pre_confirm_validators._valid_caller_warehouse_scope) ──

def test_warehouse_scoped_caller_rejects_unassigned_warehouse():
    # view_storage_history has no other pre-confirm check ahead of the
    # shared warehouse-scope rule, unlike adjust_storage (which validates
    # SKU lines first and needs a real db) -- isolates the rule under test.
    context = {"warehouse_codes": ["JFK", "NJ"]}
    error = pre_confirm_validators.run(
        "view_storage_history", context, {"warehouse_code": "DE", "start_month": "2026-01", "end_month": "2026-01"}, db=None,
    )
    assert error is not None


def test_warehouse_scoped_caller_accepts_assigned_warehouse():
    context = {"warehouse_codes": ["JFK", "NJ"]}
    error = pre_confirm_validators.run(
        "view_storage_history", context, {"warehouse_code": "JFK", "start_month": "2026-01", "end_month": "2026-01"}, db=None,
    )
    assert error is None


def test_unscoped_caller_never_blocked_by_warehouse_scope():
    """admin/accountant/customer -- warehouse_codes is None -- never restricted."""
    context = {"warehouse_codes": None}
    error = pre_confirm_validators.run(
        "view_invoice", context, {"warehouse_code": "DE", "start_month": "2026-01", "end_month": "2026-01"}, db=None,
    )
    assert error is None


def test_single_assigned_warehouse_still_works():
    context = {"warehouse_codes": ["JFK"]}
    error = pre_confirm_validators.run(
        "upsert_address",
        context,
        {"warehouse_code": "JFK", "charge_type": "delivery", "addr": "123 Main St"},
        db=None,
    )
    assert error is None


# ── core.kefu_case_adapter._authorize_case: None must stay unscoped ──────────

def test_authorize_case_none_warehouse_codes_is_never_denied():
    from core.kefu_case_adapter import _authorize_case

    access = SimpleNamespace(
        group_id="g1", allowed_services=[{"service_type_id": "svc-1"}], warehouse_codes=None,
    )
    session = SimpleNamespace(
        group_id="g1", service_type_id="svc-1", collected_fields={"warehouse_code": "DE"},
    )
    assert _authorize_case(access, session) is None


def test_authorize_case_multi_warehouse_allows_any_assigned():
    from core.kefu_case_adapter import _authorize_case

    access = SimpleNamespace(
        group_id="g1", allowed_services=[{"service_type_id": "svc-1"}], warehouse_codes=["JFK", "NJ"],
    )
    for wh in ("JFK", "NJ"):
        session = SimpleNamespace(
            group_id="g1", service_type_id="svc-1", collected_fields={"warehouse_code": wh},
        )
        assert _authorize_case(access, session) is None


def test_authorize_case_multi_warehouse_denies_unassigned():
    from core.kefu_case_adapter import _authorize_case

    access = SimpleNamespace(
        group_id="g1", allowed_services=[{"service_type_id": "svc-1"}], warehouse_codes=["JFK", "NJ"],
    )
    session = SimpleNamespace(
        group_id="g1", service_type_id="svc-1", collected_fields={"warehouse_code": "DE"},
    )
    assert _authorize_case(access, session) == "case_wrong_warehouse"
