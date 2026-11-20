"""update_address: baseline, validators, handler, displays (no DB)."""
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from core import address_update as au
from core import confirmation, pre_confirm_validators as v, result_message
from handlers.registry import HANDLER_REGISTRY
from handlers.uchoice.address import UpdateAddressHandler

TARGET_ID = str(uuid.uuid4())
OTHER_ID = str(uuid.uuid4())


def _row(address_id=TARGET_ID, **kw):
    base = dict(
        address_id=uuid.UUID(address_id), company_name="ACME", addr="1 Main St", contact=None, phone=None,
        note=None, charge_type="delivery", warehouse_code="JFK", customer_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Query:
    def __init__(self, db):
        self.db, self.wanted = db, None

    def filter_by(self, **kw):
        self.wanted = kw.get("address_id")
        return self

    def filter(self, *a, **k):
        return self

    def with_for_update(self):
        self.db.locked = True
        return self

    def first(self):
        return next((r for r in self.db.rows if str(r.address_id) == str(self.wanted)), None)

    def all(self):
        return list(self.db.rows)


class _DB:
    def __init__(self, *rows):
        self.rows, self.locked, self.flushed, self.committed = list(rows), False, False, False

    def query(self, _model):
        return _Query(self)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def get_bind(self):
        return None


def _ctx(fields, role="admin", codes=None):
    return {"collected_fields": fields, "role": role, "warehouse_codes": codes, "wechat_openid": "u1"}


def test_registry_and_v45_are_default_deny():
    assert HANDLER_REGISTRY["update_address"] is UpdateAddressHandler
    sql = (pathlib.Path(__file__).resolve().parents[2] / "db/migrations/V45__update_address_service.sql").read_text(encoding="utf-8")
    assert "INSERT INTO role_service_permission" not in sql


def test_baseline_is_frozen_per_target():
    db = _DB(_row())
    patch = au.baseline_patch(db, {"matched_address_id": TARGET_ID})
    assert patch["_baseline_id"] == TARGET_ID and patch["_baseline"]["addr"] == "1 Main St"
    assert au.baseline_patch(db, {"matched_address_id": TARGET_ID, **patch}) == {}
    db2 = _DB(_row(), _row(OTHER_ID, addr="2 Side St"))
    again = au.baseline_patch(db2, {"matched_address_id": OTHER_ID, **patch})
    assert again["_baseline"]["addr"] == "2 Side St"
    assert au.baseline_patch(db, {}) == {}


def test_effective_changes_semantics():
    cur = au.snapshot(_row(contact="Old"))
    assert au.effective_changes(cur, {"contact": "Old"}) == {}
    assert au.effective_changes(cur, {"contact": ""}) == {"contact": ("Old", None)}
    assert au.effective_changes(cur, {"addr": ""}) == {}
    assert au.effective_changes(cur, {"phone": " 555 "}) == {"phone": (None, "555")}
    assert au.effective_changes(cur, {"customer_id": "x", "junk": 1}) == {}


@pytest.mark.parametrize("fields,role,expect", [
    ({}, "admin", "未找到"),
    ({"matched_address_id": "not-a-uuid", "phone": "1"}, "admin", "未找到"),
    ({"matched_address_id": str(uuid.uuid4()), "phone": "1"}, "admin", "未找到"),
    ({"matched_address_id": TARGET_ID}, "admin", "没有检测到"),
    ({"matched_address_id": TARGET_ID, "charge_type": "delivery"}, "admin", "没有检测到"),
    ({"matched_address_id": TARGET_ID, "customer_id": "x", "phone": "1"}, "admin", "所属客户"),
    ({"matched_address_id": TARGET_ID, "addr": "  "}, "admin", "地址不能为空"),
    ({"matched_address_id": TARGET_ID, "charge_type": "self_pickup"}, "warehouse_admin", "只有管理员"),
    ({"matched_address_id": TARGET_ID, "warehouse_code": "DE"}, "customer", "只有管理员"),
    ({"matched_address_id": TARGET_ID, "phone": "1" * 51}, "admin", "电话过长"),
])
def test_validator_rejections(fields, role, expect):
    msg = v.run("update_address", _ctx(fields, role), fields, _DB(_row()))
    assert msg and expect in msg


def test_validator_accepts_allowed_edits():
    fields = {"matched_address_id": TARGET_ID, "contact": "Vivek", "phone": "516-305-9127"}
    assert v.run("update_address", _ctx(fields, "warehouse_admin", ["JFK"]), fields, _DB(_row())) is None
    admin = {"matched_address_id": TARGET_ID, "charge_type": "self_pickup", "warehouse_code": "DE"}
    assert v.run("update_address", _ctx(admin), admin, _DB(_row())) is None


def test_validator_warehouse_scope_uses_existing_and_requested():
    fields = {"matched_address_id": TARGET_ID, "phone": "1"}
    assert v.run("update_address", _ctx(fields, "warehouse_admin", ["JFK"]), fields, _DB(_row(warehouse_code="DE")))
    assert v.run("update_address", _ctx(fields, "warehouse_admin", None), fields, _DB(_row()))


def test_validator_detects_stale_baseline_and_duplicate():
    fields = {"matched_address_id": TARGET_ID, "phone": "1", "_baseline": au.snapshot(_row(company_name="OLDER"))}
    assert au.STALE_MESSAGE in v.run("update_address", _ctx(fields), fields, _DB(_row()))
    clash = _row(OTHER_ID, addr="9 Other Rd")
    edit = {"matched_address_id": TARGET_ID, "addr": "9 other rd."}
    msg = v.run("update_address", _ctx(edit), edit, _DB(_row(), clash))
    assert msg and "已存在" in msg
    solo = {"matched_address_id": TARGET_ID, "addr": "1 MAIN ST, Suite 2"}
    assert v.run("update_address", _ctx(solo), solo, _DB(_row())) is None


def test_handler_applies_only_changes_and_reports_diff():
    row = _row(contact="Old")
    db = _DB(row)
    fields = {"matched_address_id": TARGET_ID, "contact": "", "phone": " 555 "}
    out = UpdateAddressHandler().handle(_ctx(fields), {"_defer_commit": True}, db)
    assert row.contact is None and row.phone == "555" and row.addr == "1 Main St"
    assert out["mode"] == "更新" and out["changes"] == {"contact": ["Old", None], "phone": [None, "555"]}
    assert db.locked and db.flushed and not db.committed


def test_handler_rechecks_everything_at_execution():
    base = {"matched_address_id": TARGET_ID}
    with pytest.raises(RuntimeError, match="不存在"):
        UpdateAddressHandler().handle(_ctx({"matched_address_id": str(uuid.uuid4()), "phone": "1"}), {}, _DB(_row()))
    with pytest.raises(RuntimeError, match="只有管理员"):
        UpdateAddressHandler().handle(_ctx({**base, "charge_type": "self_pickup"}, "warehouse_admin", ["JFK"]), {}, _DB(_row()))
    with pytest.raises(RuntimeError):
        UpdateAddressHandler().handle(_ctx({**base, "phone": "1"}, "warehouse_admin", ["DE"]), {}, _DB(_row()))
    stale = {**base, "phone": "1", "_baseline": au.snapshot(_row(note="x"))}
    with pytest.raises(RuntimeError, match="被他人修改"):
        UpdateAddressHandler().handle(_ctx(stale), {}, _DB(_row()))
    row = _row()
    with pytest.raises(RuntimeError, match="没有检测到"):
        UpdateAddressHandler().handle(_ctx(base), {}, _DB(row))
    assert row.phone is None


def test_confirmation_and_result_show_old_to_new():
    row = _row(contact="Old")
    fields = {"matched_address_id": TARGET_ID, "contact": "New", "charge_type": "self_pickup", "_baseline": au.snapshot(row)}
    sections = confirmation._update_address_sections_builder(fields, _DB(row))
    assert sections[0]["items"]["公司"] == "ACME"
    assert sections[1]["items"]["联系人"] == "Old → New"
    assert "→" in sections[1]["items"]["计费类型"]
    res = result_message._update_address_result_sections_builder(
        {"result": {"changes": {"phone": [None, "555"], "contact": ["Old", None]}}}, None)
    assert res[0]["items"] == {"电话": "（空） → 555", "联系人": "Old → （清空）"}


def test_prompt_explains_address_book():
    from ai import prompt_builder
    text = prompt_builder._address_book_instructions()
    assert "address_book" in text and "matched_address_id" in text and "仅管理员" in text
