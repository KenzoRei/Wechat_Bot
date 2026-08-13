"""
core/pre_confirm_validators.py's _valid_destination_address_required must
reject a destination_address_id whose OWN warehouse_code doesn't match the
request's own warehouse_code -- the correctness backstop for the live
incident where two addresses shared the identical physical addr and
differed only by which warehouse's outbound requests they were meant for
(e.g. "DE Warehouse", warehouse_code=JFK, a real JFK->DE inter-warehouse
transfer vs. a same-address DE-only self-pickup entry that does NOT credit
any warehouse's storage). Nothing previously stopped a JFK-sourced request
from confirming with the DE-scoped address. This is the backstop that
matters even if the AI candidate-list scoping (core/uchoice_context.py's
address_candidates) ever fails to prevent the wrong choice from reaching
here in the first place.
"""
from types import SimpleNamespace

from core.pre_confirm_validators import _valid_destination_address_required


class _Query:
    def __init__(self, addr):
        self._addr = addr

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._addr


class _DB:
    def __init__(self, addr):
        self._addr = addr

    def query(self, _model):
        return _Query(self._addr)


def _addr(warehouse_code, company_name="Some Co", addr_text="1 Main St"):
    return SimpleNamespace(company_name=company_name, addr=addr_text, warehouse_code=warehouse_code)


def test_rejects_address_scoped_to_a_different_warehouse():
    db = _DB(_addr(warehouse_code="DE", company_name="DE-Scoped Co"))
    fields = {"destination_address_id": "11111111-1111-1111-1111-111111111111", "warehouse_code": "JFK"}

    error = _valid_destination_address_required({}, fields, db)

    assert error is not None
    assert "DE-Scoped Co" in error
    assert "JFK" in error


def test_accepts_address_scoped_to_the_same_warehouse():
    db = _DB(_addr(warehouse_code="JFK"))
    fields = {"destination_address_id": "11111111-1111-1111-1111-111111111111", "warehouse_code": "JFK"}

    assert _valid_destination_address_required({}, fields, db) is None


def test_accepts_warehouse_agnostic_address_regardless_of_source():
    """A null addr.warehouse_code (e.g. '散客'/walk-in) is not warehouse-specific."""
    db = _DB(_addr(warehouse_code=None))
    fields = {"destination_address_id": "11111111-1111-1111-1111-111111111111", "warehouse_code": "JFK"}

    assert _valid_destination_address_required({}, fields, db) is None


def test_skips_the_check_when_request_warehouse_code_not_yet_known():
    """Ordering safety net: this validator must not crash or falsely reject
    if it somehow ran before warehouse_code was resolved (in practice
    _resolve_outbound_warehouse_default always runs first)."""
    db = _DB(_addr(warehouse_code="DE"))
    fields = {"destination_address_id": "11111111-1111-1111-1111-111111111111"}

    assert _valid_destination_address_required({}, fields, db) is None


def test_not_found_still_takes_priority_over_warehouse_check():
    db = _DB(None)
    fields = {"destination_address_id": "11111111-1111-1111-1111-111111111111", "warehouse_code": "JFK"}

    error = _valid_destination_address_required({}, fields, db)

    assert error is not None
    assert "未能识别" in error
