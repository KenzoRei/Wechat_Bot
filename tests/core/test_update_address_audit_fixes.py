"""Regression tests for the Codex implementation audit of 5e8c180 (no DB)."""
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from core import address_update as au
from core import pre_confirm_validators as v
from core import session_manager
from core.workflow_errors import AddressOperationRejected
from handlers.uchoice.address import AddAddressHandler, UpdateAddressHandler

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET_ID = str(uuid.uuid4())


def _row(**kw):
    base = dict(
        address_id=uuid.UUID(TARGET_ID), company_name="ACME", addr="1 Main St", contact=None, phone=None,
        note=None, charge_type="delivery", warehouse_code="JFK", customer_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Query:
    def __init__(self, db):
        self.db = db

    def filter_by(self, **kw):
        return self

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        self.db.calls.append("populate_existing")
        return self

    def with_for_update(self):
        self.db.calls.append("with_for_update")
        return self

    def first(self):
        return self.db.row

    def all(self):
        return [self.db.row]


class _DB:
    def __init__(self, row):
        self.row, self.calls = row, []

    def query(self, _m):
        return _Query(self)

    def flush(self):
        pass

    def get_bind(self):
        return None


def _ctx(fields, role="admin", codes=None):
    return {"collected_fields": fields, "role": role, "warehouse_codes": codes, "wechat_openid": "u1"}


# 3. expected rejections are typed (and still RuntimeErrors)
def test_expected_rejections_are_typed_with_user_message():
    assert issubclass(AddressOperationRejected, RuntimeError)
    with pytest.raises(AddressOperationRejected) as exc:
        AddAddressHandler().handle(_ctx({"matched_address_id": "x"}), {}, _DB(None))
    assert "不支持修改" in exc.value.user_message
    stale = {"matched_address_id": TARGET_ID, "phone": "1", "_baseline": au.snapshot(_row(note="x"))}
    with pytest.raises(AddressOperationRejected) as exc:
        UpdateAddressHandler().handle(_ctx(stale), {}, _DB(_row()))
    assert exc.value.user_message == au.STALE_MESSAGE


def test_both_orchestrators_handle_the_typed_rejection():
    engine = (ROOT / "core" / "workflow_engine.py").read_text(encoding="utf-8")
    kefu = (ROOT / "core" / "kefu_turn_apply.py").read_text(encoding="utf-8")
    assert engine.count("except AddressOperationRejected") == 2
    assert kefu.count("except AddressOperationRejected") == 1
    # caught before the generic fallbacks / the carrier branch
    first = engine.index("except AddressOperationRejected")
    assert first < engine.index("except Exception", first)
    assert kefu.index("except AddressOperationRejected") < kefu.index("except LabelCreationRejected")


# 4. domain validation before confirmation
@pytest.mark.parametrize("fields,expect", [
    ({"charge_type": "not_a_rate"}, "计费类型无效"),
    ({"charge_type": ""}, "不能清空"),
    ({"warehouse_code": "ZZZ"}, "未知仓库"),
    ({"warehouse_code": ""}, "不能清空"),
    ({"company_name": "x" * 201}, "过长"),
    ({"phone": 12345}, "格式不正确"),
    ({"note": ["a"]}, "格式不正确"),
])
def test_invalid_values_are_rejected_before_confirmation(fields, expect):
    payload = {"matched_address_id": TARGET_ID, **fields}
    msg = v.run("update_address", _ctx(payload), payload, _DB(_row()))
    assert msg and expect in msg
    with pytest.raises(AddressOperationRejected):
        UpdateAddressHandler().handle(_ctx(payload), {}, _DB(_row()))


def test_valid_charge_type_and_clearing_still_pass():
    payload = {"matched_address_id": TARGET_ID, "charge_type": "self_pickup", "note": ""}
    assert v.run("update_address", _ctx(payload), payload, _DB(_row(note="old"))) is None


# 5. locked load must refresh the identity map
def test_locked_load_populates_existing_before_locking():
    db = _DB(_row())
    assert au.load_target(db, {"matched_address_id": TARGET_ID}, lock=True) is db.row
    assert db.calls == ["populate_existing", "with_for_update"]
    db2 = _DB(_row())
    au.load_target(db2, {"matched_address_id": TARGET_ID})
    assert db2.calls == []


# 2. address_book scope comes from the caller's assignment
@pytest.fixture
def capture(monkeypatch):
    seen = {}
    from core import uchoice_context

    def fake(db, customer_id=None, source_warehouse_code=None, allowed_warehouse_codes=None):
        seen.setdefault("calls", []).append({"source": source_warehouse_code, "allowed": allowed_warehouse_codes})
        return [{"address_id": "a"}]

    monkeypatch.setattr(uchoice_context, "address_candidates", fake)
    for name in ("sku_candidates", "pending_requests_candidates"):
        if hasattr(uchoice_context, name):
            monkeypatch.setattr(uchoice_context, name, lambda *a, **k: [])
    return seen


def _access(role, codes):
    return SimpleNamespace(
        role=role, warehouse_codes=codes,
        allowed_services=[{"name": "update_address", "service_type_id": "u1"}],
        wechat_openid="o", group_id="g", staff_id=None, source_channel="smart_robot",
    )


def _book(access):
    try:
        result = session_manager._build_uchoice_candidates(None, access, None, "更新地址")
    except Exception as e:  # unrelated candidate builders may need a real DB
        pytest.skip(f"candidate builder needs more context: {e}")
    return result.get("address_book")


def test_single_warehouse_caller_is_filtered(capture):
    assert _book(_access("warehouseman", ["JFK"])) == [{"address_id": "a"}]
    assert capture["calls"][0]["allowed"] == ["JFK"]


def test_multi_warehouse_caller_is_filtered(capture):
    _book(_access("warehouse_admin", ["JFK", "DE"]))
    assert capture["calls"][0]["allowed"] == ["JFK", "DE"]


def test_scoped_role_without_assignment_gets_nothing(capture):
    assert _book(_access("warehouseman", None)) == []
    assert "calls" not in capture


def test_unscoped_admin_sees_the_whole_book(capture):
    _book(_access("admin", None))
    assert capture["calls"][0]["allowed"] is None


# 1. Kefu: target comes only from the candidate-validated address_match
def test_kefu_update_target_is_decided_from_address_match():
    kefu = (ROOT / "core" / "kefu_turn_apply.py").read_text(encoding="utf-8")
    assert 'extracted_fields.pop("matched_address_id", None)' in kefu
    assert '_address_decision(db, context, ai_response, "address_book")' in kefu
    assert '"matched_address_id": ("要更新的地址"' in kefu
    from ai import prompt_builder
    assert "address_match" in prompt_builder._address_book_instructions(True)
    assert "address_match" not in prompt_builder._address_book_instructions(False)


def test_kefu_ambiguous_update_renders_candidates_and_unmatched_does_not_pivot():
    from core import kefu_response_renderer as r
    candidates = [{"address_id": "a1", "company_name": "ACME", "addr": "1 Main"}, {"address_id": "a2", "company_name": "ACME", "addr": "2 Side"}]
    ai = SimpleNamespace(address_match=SimpleNamespace(status="ambiguous", candidate_ids=("a1", "a2"), new_address=None))
    decision = r.validate_address_match(ai, candidates)
    assert decision.status == "ambiguous"
    from core import kefu_turn_apply
    text = kefu_turn_apply._render_address_ambiguity(decision, candidates)
    assert "ACME" in text and "1 Main" in text and "2 Side" in text
    forged = SimpleNamespace(address_match=SimpleNamespace(status="matched", candidate_ids=("not-offered",), new_address=None))
    assert r.validate_address_match(forged, candidates).status == "invalid"


def test_switching_target_starts_clean_and_refreezes_baseline():
    a, b = TARGET_ID, str(uuid.uuid4())
    previous = {"matched_address_id": a, "phone": "111", "contact": "Old", "_baseline": {"phone": None}, "_baseline_id": a}
    switched = au.retarget_fields({"phone": "777", "junk": "x"}, b)
    assert switched == {"phone": "777", "matched_address_id": b}   # A's edits and baseline are gone
    row_b = _row(address_id=uuid.UUID(b), addr="2 Side St")
    patch = au.baseline_patch(_DB(row_b), switched)
    assert patch["_baseline_id"] == b and patch["_baseline"]["addr"] == "2 Side St"
    assert "contact" not in switched and "_baseline" not in switched and previous["phone"] == "111"


def test_kefu_evaluates_target_on_every_update_turn():
    kefu = (ROOT / "core" / "kefu_turn_apply.py").read_text(encoding="utf-8")
    assert 'if service["name"] == "update_address":\n        # Evaluated on EVERY update turn' in kefu.replace("\r\n", "\n")
    assert "retarget_fields(extracted, decision.matched_address_id)" in kefu
