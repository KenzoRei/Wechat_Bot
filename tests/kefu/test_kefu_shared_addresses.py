"""
kefu-deterministic-response-plan.md Sec 5: core.uchoice_context
.address_candidates()'s customer_id-filter-optional behavior. A minimal,
purpose-built mock DB -- this function's query shape is simple enough that
the shared tests/uchoice_self_registration/_kefu_mock_db.py machinery (built
for staff/role lookups) isn't a good fit. Real-database sharing/scoping
proof lives in tests/kefu_integration/test_kefu_shared_address_book.py.
"""
from types import SimpleNamespace

from core.uchoice_context import address_candidates


class _FakeAddressQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = {}

    def filter_by(self, **kwargs):
        self._filters.update(kwargs)
        return self

    def all(self):
        if "customer_id" not in self._filters:
            return list(self._rows)
        wanted = self._filters["customer_id"]
        return [r for r in self._rows if r.customer_id == wanted]


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        return _FakeAddressQuery(self._rows)


def _addr(address_id, customer_id, company_name="Co"):
    return SimpleNamespace(
        address_id=address_id, company_name=company_name, charge_type="delivery",
        addr="1 Main St", warehouse_code="JFK", note=None, customer_id=customer_id,
    )


def test_no_filter_returns_every_address_regardless_of_customer():
    rows = [_addr("a1", "cust-1"), _addr("a2", "cust-2"), _addr("a3", None)]
    db = _FakeDB(rows)
    result = address_candidates(db)
    assert {r["address_id"] for r in result} == {"a1", "a2", "a3"}


def test_explicit_customer_filter_still_works_for_legacy_callers():
    rows = [_addr("a1", "cust-1"), _addr("a2", "cust-2")]
    db = _FakeDB(rows)
    result = address_candidates(db, customer_id="cust-1")
    assert {r["address_id"] for r in result} == {"a1"}


def test_returned_shape_omits_customer_id_field():
    """The candidate payload sent to the AI must never carry customer_id
    (kefu-deterministic-response-plan.md Sec 5.3) -- only display fields."""
    db = _FakeDB([_addr("a1", "cust-1")])
    result = address_candidates(db)
    assert set(result[0].keys()) == {"address_id", "company_name", "charge_type", "addr", "warehouse_code", "note"}
