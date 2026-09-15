"""
Regression coverage for the 2026-09-15 role-permission-attribute-architecture
audit's three findings on warehouse-scope enforcement
(.collab/tasks/role-permission-attribute-architecture.md):

1. A warehouse-scoped caller (warehouseman/warehouse_admin) with no
   warehouse_codes assigned must be rejected outright -- not treated as
   unrestricted, the previous "codes is None" inference.
2. Rejecting a completion attempt must never mark the ORIGINAL target
   request failed -- handlers/uchoice/lookup_validate.py and
   handlers/uchoice/storage_txns.py's completion handlers must raise
   core.workflow_errors.TargetValidationError, not a bare RuntimeError,
   so core/workflow_engine.py's generic exception handler doesn't call
   mark_failed() on session.request_log_id (which is the TARGET for a
   targets_existing_request session, not something this session owns).
3. handlers/uchoice/address.py must apply the same fail-closed check on
   BOTH the create and update paths, not just skip validation when scope
   is missing.
"""
import pytest

from core.workflow_errors import TargetValidationError
from handlers.uchoice.address import UpsertAddressHandler
from handlers.uchoice.storage_txns import ApplyInboundStorageHandler, ApplyOutboundStorageHandler
from handlers.uchoice.lookup_validate import LookupAndValidateCompletionHandler
from models.request_log import RequestLog
from models.service import ServiceType
from models.uchoice import UchoiceAddress


# ── UpsertAddressHandler ──────────────────────────────────────────────────

class _FakeAddressQuery:
    def __init__(self, existing):
        self.existing = existing

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.existing


class _FakeAddressDB:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    def query(self, model):
        assert model is UchoiceAddress
        return _FakeAddressQuery(self.existing)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def flush(self):
        pass

    def refresh(self, obj):
        pass


def test_upsert_address_update_rejects_scoped_caller_with_missing_codes():
    existing = UchoiceAddress(
        address_id="33333333-0000-0000-0000-000000000003",
        company_name="OLD", charge_type="delivery", addr="OLD ADDR", warehouse_code="NJ",
    )
    context = {
        "collected_fields": {"matched_address_id": str(existing.address_id), "company_name": "NEW"},
        "wechat_openid": "caller",
        "customer_id": None,
        "role": "warehouse_admin",
        "warehouse_codes": None,  # scoped role, no assignment data
    }
    db = _FakeAddressDB(existing=existing)
    with pytest.raises(RuntimeError):
        UpsertAddressHandler().handle(context, {}, db)
    assert existing.company_name == "OLD"  # never mutated
    assert not db.committed


def test_upsert_address_create_rejects_scoped_caller_with_missing_codes():
    context = {
        "collected_fields": {"company_name": "ACME", "charge_type": "truck_transfer", "addr": "1 Main St", "warehouse_code": "NJ"},
        "wechat_openid": "caller",
        "customer_id": None,
        "role": "warehouse_admin",
        "warehouse_codes": [],  # scoped role, empty assignment
    }
    db = _FakeAddressDB()
    with pytest.raises(RuntimeError):
        UpsertAddressHandler().handle(context, {}, db)
    assert db.added == []


def test_upsert_address_create_allows_unrestricted_caller_with_no_codes():
    context = {
        "collected_fields": {"company_name": "ACME", "charge_type": "truck_transfer", "addr": "1 Main St", "warehouse_code": "NJ"},
        "wechat_openid": "caller",
        "customer_id": None,
        "role": "customer",
        "warehouse_codes": None,
    }
    db = _FakeAddressDB()
    result = UpsertAddressHandler().handle(context, {}, db)
    assert result["mode"] == "新增"
    assert db.added and db.committed


def test_upsert_address_update_rejects_out_of_scope_target_warehouse():
    existing = UchoiceAddress(
        address_id="44444444-0000-0000-0000-000000000004",
        company_name="OLD", charge_type="delivery", addr="OLD ADDR", warehouse_code="NJ",
    )
    context = {
        "collected_fields": {"matched_address_id": str(existing.address_id), "warehouse_code": "NJ"},
        "wechat_openid": "caller",
        "customer_id": None,
        "role": "warehouse_admin",
        "warehouse_codes": ["JFK"],  # assigned, but not to NJ
    }
    db = _FakeAddressDB(existing=existing)
    with pytest.raises(RuntimeError):
        UpsertAddressHandler().handle(context, {}, db)
    assert existing.warehouse_code == "NJ"


# ── ApplyInboundStorageHandler / ApplyOutboundStorageHandler ────────────────

def test_apply_inbound_storage_raises_target_validation_error_not_runtime_error():
    context = {
        "_uchoice_target": {"warehouse_code": "NJ"},
        "role": "warehouse_admin",
        "warehouse_codes": None,
    }
    with pytest.raises(TargetValidationError):
        ApplyInboundStorageHandler().handle(context, {}, db=None)


def test_apply_outbound_storage_raises_target_validation_error_not_runtime_error():
    context = {
        "_uchoice_target": {"warehouse_code": "NJ"},
        "role": "warehouse_admin",
        "warehouse_codes": [],
    }
    with pytest.raises(TargetValidationError):
        ApplyOutboundStorageHandler().handle(context, {}, db=None)


# ── LookupAndValidateCompletionHandler ──────────────────────────────────────

class _FakeRequestLog:
    def __init__(self, status="processing", service_type_id="svc-inbound", origin_session_id=None):
        self.log_id = "log-1"
        self.status = status
        self.serial_number = "IN-001"
        self.service_type_id = service_type_id
        self.origin_session_id = origin_session_id
        self.wechat_openid = "caller"
        self.group_id = None


class _FakeServiceType:
    def __init__(self, name):
        self.service_type_id = "svc-inbound"
        self.name = name


class _ChainableQuery:
    """Supports .filter_by(...).populate_existing().with_for_update().first(), returning a fixed value."""

    def __init__(self, result):
        self.result = result

    def filter_by(self, **kwargs):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.result


class _FakeLookupDB:
    def __init__(self, target, service_type):
        self.target = target
        self.service_type = service_type

    def query(self, model):
        if model is RequestLog:
            return _ChainableQuery(self.target)
        if model is ServiceType:
            return _ChainableQuery(self.service_type)
        raise AssertionError(f"unexpected query for {model}")


def test_lookup_completion_rejects_scoped_caller_with_missing_codes_without_touching_target():
    target = _FakeRequestLog(status="processing")
    service_type = _FakeServiceType("uchoice_inbound_request")
    db = _FakeLookupDB(target, service_type)
    context = {
        "request_log_id": "log-1",
        "role": "warehouse_admin",
        "warehouse_codes": None,  # scoped role, no assignment data
    }
    with pytest.raises(TargetValidationError):
        LookupAndValidateCompletionHandler().handle(context, {"direction": "inbound"}, db)
    # The target's own status must be left completely untouched -- proving
    # this rejection never reaches (and never needs) mark_failed() on it.
    assert target.status == "processing"


def test_lookup_completion_allows_unrestricted_caller_with_no_codes():
    target = _FakeRequestLog(status="processing")
    service_type = _FakeServiceType("uchoice_inbound_request")
    db = _FakeLookupDB(target, service_type)
    context = {
        "request_log_id": "log-1",
        "role": "admin",
        "warehouse_codes": None,
    }
    result = LookupAndValidateCompletionHandler().handle(context, {"direction": "inbound"}, db)
    assert result == {"warehouse_code": None}
    assert context["_uchoice_target"]["serial_number"] == "IN-001"
