"""
core/uchoice_customer.py — pure-logic resolve/lock behavior, no DB needed.
kefu-migration-plan.md Sec 6.2 / discussion.md round 98.
"""
from types import SimpleNamespace

from core.uchoice_customer import resolve_and_lock_customer

_CANDIDATES = [
    {"customer_id": "11111111-1111-1111-1111-111111111111", "customer_code": "ACME", "canonical_name": "Acme Corp", "aliases": []},
    {"customer_id": "22222222-2222-2222-2222-222222222222", "customer_code": "BETA", "canonical_name": "Beta LLC", "aliases": ["贝塔"]},
]


def _session(customer_id=None):
    return SimpleNamespace(customer_id=customer_id)


def test_returns_none_when_unresolved_and_no_candidate_extracted():
    session = _session()
    result = resolve_and_lock_customer(session, {}, _CANDIDATES)
    assert result is None
    assert session.customer_id is None


def test_locks_a_valid_extracted_customer_id():
    session = _session()
    result = resolve_and_lock_customer(session, {"customer_id": _CANDIDATES[0]["customer_id"]}, _CANDIDATES)
    assert result == _CANDIDATES[0]["customer_id"]
    assert str(session.customer_id) == _CANDIDATES[0]["customer_id"]


def test_rejects_a_hallucinated_customer_id_not_in_the_real_candidate_list():
    session = _session()
    result = resolve_and_lock_customer(session, {"customer_id": "99999999-9999-9999-9999-999999999999"}, _CANDIDATES)
    assert result is None
    assert session.customer_id is None


def test_already_locked_customer_id_is_returned_and_never_overwritten():
    session = _session(customer_id="11111111-1111-1111-1111-111111111111")
    # Even though this turn's extracted_fields names a DIFFERENT valid
    # customer, the already-locked one wins -- round 98: locked once, never
    # re-resolved or drifted by a later turn.
    result = resolve_and_lock_customer(session, {"customer_id": _CANDIDATES[1]["customer_id"]}, _CANDIDATES)
    assert result == "11111111-1111-1111-1111-111111111111"
    assert str(session.customer_id) == "11111111-1111-1111-1111-111111111111"


def test_empty_candidate_list_still_returns_the_already_locked_value():
    session = _session(customer_id="11111111-1111-1111-1111-111111111111")
    result = resolve_and_lock_customer(session, {}, [])
    assert result == "11111111-1111-1111-1111-111111111111"
