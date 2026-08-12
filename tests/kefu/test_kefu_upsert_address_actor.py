"""
Live incident: every Kefu-originated new address crashed on uchoice_address's
NOT NULL created_by constraint (msgid B7AMS7ixMesqF5r4f4DWZ6DAa3) --
UpsertAddressHandler only ever read context["wechat_openid"], which is
always None for Kefu (Kefu has no such identity), unlike
handlers/uchoice/storage_txns.py's _actor_id, which already had the
submitted_by_staff_id fallback.
"""
import pytest

from handlers.uchoice.address import UpsertAddressHandler
from models.uchoice import UchoiceAddress


class _FakeQuery:
    def __init__(self, existing):
        self.existing = existing

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.existing


class _FakeDB:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    def query(self, model):
        assert model is UchoiceAddress
        return _FakeQuery(self.existing)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def flush(self):
        pass

    def refresh(self, obj):
        pass


_FIELDS = {"company_name": "ACME", "charge_type": "truck_transfer", "addr": "1 Main St", "warehouse_code": "JFK"}


def test_kefu_new_address_falls_back_to_submitted_by_staff_id():
    context = {
        "collected_fields": _FIELDS,
        "wechat_openid": None,
        "submitted_by_staff_id": "11111111-0000-0000-0000-000000000001",
        "customer_id": None,
    }
    db = _FakeDB()
    result = UpsertAddressHandler().handle(context, {}, db)
    assert result["mode"] == "新增"
    assert db.added[0].created_by == "11111111-0000-0000-0000-000000000001"
    assert db.committed


def test_smart_robot_new_address_still_uses_wechat_openid():
    context = {
        "collected_fields": _FIELDS,
        "wechat_openid": "smart-robot-openid",
        "customer_id": None,
    }
    db = _FakeDB()
    result = UpsertAddressHandler().handle(context, {}, db)
    assert result["mode"] == "新增"
    assert db.added[0].created_by == "smart-robot-openid"


def test_new_address_with_no_resolvable_actor_raises_instead_of_crashing_db():
    context = {"collected_fields": _FIELDS, "wechat_openid": None, "customer_id": None}
    db = _FakeDB()
    with pytest.raises(RuntimeError):
        UpsertAddressHandler().handle(context, {}, db)
    assert db.added == []


def test_update_path_never_requires_an_actor():
    existing = UchoiceAddress(
        address_id="22222222-0000-0000-0000-000000000002",
        company_name="OLD", charge_type="delivery", addr="OLD ADDR", warehouse_code="JFK",
    )
    context = {
        "collected_fields": {**_FIELDS, "matched_address_id": str(existing.address_id)},
        "wechat_openid": None,
        "customer_id": None,
    }
    db = _FakeDB(existing=existing)
    result = UpsertAddressHandler().handle(context, {}, db)
    assert result["mode"] == "更新"
    assert existing.company_name == "ACME"
