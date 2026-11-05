"""add_address duplicate guard: normalisation, key, validator, handler (no DB)."""
from types import SimpleNamespace

import pytest

from core import address_dedupe as dd
from core import pre_confirm_validators as v
from handlers.uchoice.address import AddAddressHandler


def test_normalize_is_conservative_about_unit_numbers():
    assert dd.normalize_addr(" 1309 E Bay Ave,  Bronx, NY. 10474 ") == "1309 e bay ave bronx ny 10474"
    assert dd.normalize_addr("Apt 1-2") != dd.normalize_addr("Apt 12")
    assert dd.normalize_addr("Unit #5") != dd.normalize_addr("Unit 5")
    # no abbreviation magic: Street != St
    assert dd.normalize_addr("1 Main Street") != dd.normalize_addr("1 Main St")


def test_key_is_null_safe_and_includes_warehouse_and_charge_type():
    a = dd.duplicate_key(None, "1 Main St", None, "delivery")
    assert a == dd.duplicate_key(None, "1  main st.", "", "delivery")
    assert a != dd.duplicate_key(None, "1 Main St", "JFK", "delivery")
    assert a != dd.duplicate_key(None, "1 Main St", None, "self_pickup")
    assert a != dd.duplicate_key("c1", "1 Main St", None, "delivery")


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    def query(self, _model):
        return _Query(self.rows)

    def get_bind(self):
        return None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def _row(addr="1 Main St", warehouse="JFK", charge="delivery", customer=None, company="ACME"):
    return SimpleNamespace(addr=addr, warehouse_code=warehouse, charge_type=charge, customer_id=customer, company_name=company)


def test_find_duplicate_matches_normalised_same_key_only():
    assert dd.find_duplicate(_DB([_row("1 MAIN ST.")]), None, "1 main st", "JFK", "delivery") is not None
    assert dd.find_duplicate(_DB([_row(charge="self_pickup")]), None, "1 main st", "JFK", "delivery") is None
    assert dd.find_duplicate(_DB([_row(addr="2 Main St")]), None, "1 main st", "JFK", "delivery") is None


FIELDS = {"addr": "1 Main St", "warehouse_code": "JFK", "charge_type": "delivery", "company_name": "ACME"}


def test_validator_blocks_duplicate_and_edit_signal():
    msg = v.run("add_address", {"role": "admin"}, dict(FIELDS), _DB([_row()]))
    assert msg and "已存在" in msg and "更新地址" in msg
    assert v.run("add_address", {"role": "admin"}, {**FIELDS, "matched_address_id": "x"}, _DB([])) is not None
    assert v.run("add_address", {"role": "admin"}, dict(FIELDS), _DB([])) is None
    # incomplete drafts are not judged yet
    assert v.run("add_address", {"role": "admin"}, {"addr": "1 Main St"}, _DB([_row()])) is None


def _ctx(fields):
    return {"collected_fields": fields, "role": "admin", "warehouse_codes": None, "wechat_openid": "u1"}


def test_handler_rejects_leftover_matched_address_id():
    with pytest.raises(RuntimeError):
        AddAddressHandler().handle(_ctx({**FIELDS, "matched_address_id": "abc"}), {"_defer_commit": True}, _DB([]))


def test_handler_rechecks_duplicate_at_execution_time():
    with pytest.raises(RuntimeError, match="已存在"):
        AddAddressHandler().handle(_ctx(dict(FIELDS)), {"_defer_commit": True}, _DB([_row()]))


def test_handler_creates_when_unique_and_trims_contact():
    db = _DB([])
    out = AddAddressHandler().handle(_ctx({**FIELDS, "contact": "  Vivek ", "phone": "   "}), {"_defer_commit": True}, db)
    assert out["mode"] == "新增"
    assert db.added[0].contact == "Vivek" and db.added[0].phone is None
