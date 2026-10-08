"""
柜号 / 拆柜费 rules, offline (core/inbound_container.py; plan:
docs/ai-collaboration/2026-10-unpacking-fee/plan.md rev 5).
"""
from decimal import Decimal

import pytest

from core import inbound_container as ic


# ── 柜号 normalizing and format ───────────────────────────────────────────────

@pytest.mark.parametrize("raw, normalized", [
    ("MSCU1234567", "MSCU1234567"), ("mscu1234567", "MSCU1234567"), ("MSCU 123-4567", "MSCU1234567"),
    ("无", "无"), ("没有", "无"), ("None", "无"), ("n/a", "无"), ("-", "无"),
    ("XYZ123", "XYZ123"), ("", None), (None, None), (123, None),
])
def test_normalize_container(raw, normalized):
    assert ic.normalize_container(raw) == normalized


@pytest.mark.parametrize("value, standard", [
    ("MSCU1234567", True), ("XYZ123", False), ("MSCU123456", False), ("MSCU12345678", False), ("无", False),
])
def test_standard_format(value, standard):
    assert ic.is_standard(value) is standard


# ── Message scanning (R4/R6/R8/R9) ────────────────────────────────────────────

@pytest.mark.parametrize("message, valid, malformed", [
    ("450", ["450"], []), ("$450", ["450"], []), ("450元", ["450"], []), ("450 USD", ["450"], []),
    ("1,200", ["1200"], []), ("450.50", ["450.50"], []), ("0", ["0"], []), ("不收拆柜费", ["0"], []),
    ("45.555", [], ["45.555"]), ("-5", [], ["-5"]),
    ("四百五", [], []), ("300 450", [], []),
    ("柜号 MSCU1234567 拆柜费 450", ["450"], []),                       # container digits never a fee
    ("S2 72箱 2托，拆柜费 450", ["450"], []),                            # SKU code and quantities excluded
    ("REQ-20261008-000141 拆柜费 450", ["450"], []),                     # request ID excluded
    ("拆柜费：45000", ["45000"], []), ("收450", ["450"], []),
])
def test_amount_candidates(message, valid, malformed):
    got_valid, got_malformed = ic.amount_candidates(message)
    assert got_valid == [Decimal(v) for v in valid] and got_malformed == malformed


@pytest.mark.parametrize("message, answering, expected", [
    ("XYZ123", True, ["XYZ123"]),                       # R9: any shape when answering
    ("MSCU 123-4567", True, ["MSCU1234567"]),
    ("柜号 XYZ123 拆柜费 450", False, ["XYZ123"]),        # anchored: any shape
    ("S2 72箱，XYZ123", False, []),                      # unanchored non-container shape ignored
    ("S2 72箱 MSCU1234567", False, ["MSCU1234567"]),
    ("MSCU1234567 和 TGHU7654321", False, ["MSCU1234567", "TGHU7654321"]),
    ("无", True, ["无"]), ("入库 2托，没有柜号", False, ["无"]), ("柜号：无", False, ["无"]),
    ("REQ-20261008-000141", True, []),
])
def test_container_candidates(message, answering, expected):
    assert ic.container_candidates(message, answering=answering) == expected


# ── apply_container / apply_fee (R3/R4/R8, D3, Q1) ────────────────────────────

def test_typed_container_wins_over_the_ais_correction():
    fields, reply = ic.apply_container({}, "MSCU1234567", "MSCU123456", answering=True)
    assert fields[ic.PENDING_KEY] == {"field": "container_number", "value": "MSCU123456"}
    assert "MSCU123456" in reply and "不是标准格式" in reply


def test_standard_container_is_accepted_silently():
    fields, reply = ic.apply_container({}, "MSCU1234567", "mscu 1234567", answering=True)
    assert (fields["container_number"], reply) == ("MSCU1234567", None)


def test_two_containers_ask_again():
    fields, reply = ic.apply_container({}, "MSCU1234567", "MSCU1234567 和 TGHU7654321", answering=False)
    assert "container_number" not in fields and reply == ic.ASK_CONTAINER_AGAIN


def test_container_the_ai_made_up_asks_again():
    fields, reply = ic.apply_container({"container_number": "OLD"}, "MSCU1234567", "好的", answering=False)
    assert "container_number" not in fields and reply == ic.ASK_CONTAINER_AGAIN


@pytest.mark.parametrize("message, ai, fee", [("450", 450, 450), ("$450", "450", 450), ("450元", 450, 450),
                                              ("1,200", 1200, 1200), ("450.5", 450.5, 450.5), ("0", 0, 0)])
def test_fee_matching_the_message_is_accepted(message, ai, fee):
    fields, reply = ic.apply_fee({}, ai, message)
    assert (fields["unpacking_fee"], reply) == (fee, None)


@pytest.mark.parametrize("message, ai", [("四百五", 450), ("柜号 MSCU1234567 拆柜费 450", 1234567),
                                         ("S2 72箱 2托，拆柜费 450", 72), ("300 450", 300)])
def test_fee_not_in_the_message_asks_again(message, ai):
    fields, reply = ic.apply_fee({"unpacking_fee": 100}, ai, message)
    assert "unpacking_fee" not in fields and reply == ic.ASK_FEE_AGAIN


def test_malformed_fee_is_rejected_out_loud_even_if_the_ai_rounded_it():
    fields, reply = ic.apply_fee({"unpacking_fee": 450}, 45.55, "45.555")
    assert "unpacking_fee" not in fields and fields[ic.INVALID_FEE_KEY] is True
    assert reply == "拆柜费金额无效：45.555（须为数字，最多两位小数），请重新输入。"


def test_valid_fee_clears_the_invalid_marker():
    fields, _ = ic.apply_fee({ic.INVALID_FEE_KEY: True}, 500, "500")
    assert fields == {"unpacking_fee": 500}


def test_fee_over_cap_opens_a_check_and_accepting_it_sticks():
    fields, reply = ic.apply_fee({}, 45000, "拆柜费 45000")
    assert fields[ic.PENDING_KEY] == {"field": "unpacking_fee", "value": 45000} and "$45,000" in reply
    fields, reply = ic.resolve_pending(fields, accept=True)
    assert fields["unpacking_fee"] == 45000 and reply is None and ic.PENDING_KEY not in fields
    again, reply = ic.apply_fee(fields, 45000, "拆柜费 45000")      # confirmed once: not asked again
    assert again["unpacking_fee"] == 45000 and reply is None


def test_rejecting_a_check_clears_the_value_and_asks_again():
    fields, _ = ic.apply_container({}, "XYZ123", "XYZ123", answering=True)
    fields, reply = ic.resolve_pending(fields, accept=False)
    assert "container_number" not in fields and reply == ic.CONTAINER_QUESTION
    assert fields[ic.ASKED_KEY] == "container_number"


@pytest.mark.parametrize("text, negative", [("否", True), ("不对", True), ("no", True), ("是", False), ("XYZ", False)])
def test_negative_replies(text, negative):
    assert ic.is_negative(text) is negative


def test_effective_container_prefers_the_warehouses():
    assert ic.effective_container({"container_number": "TGHU7654321"}, {"container_number": "无"}) == "TGHU7654321"
    assert ic.effective_container({}, {"container_number": "MSCU1234567"}) == "MSCU1234567"


@pytest.mark.parametrize("value, text", [(450, "$450"), (450.5, "$450.50"), (45000, "$45,000"), (None, "$0")])
def test_format_fee(value, text):
    assert ic.format_fee(value) == text


# ── Display and prompt ───────────────────────────────────────────────────────

def test_request_confirmation_always_shows_the_container(monkeypatch):
    import core.confirmation
    from core.confirmation import build_confirmation_sections
    monkeypatch.setattr(core.confirmation, "_sku_label_map", lambda db: {})
    items = [i for s in build_confirmation_sections("uchoice_inbound_request", {
        "warehouse_code": "JFK", "sku_lines": [], "container_number": "无"}, None) for i in s["items"]]
    assert "柜号：无" in items and not any("拆包" in str(i) for i in items)


def test_prompt_drops_needs_unpacking_and_adds_container_rules():
    from ai.prompt_builder import build_system_prompt
    prompt = build_system_prompt({
        "display_name": "Staff", "role": "customer", "collected_fields": {}, "session_id": None,
        "session_status": None, "group_context": None, "allowed_services": [], "uchoice_candidates": {},
    })
    assert "needs_unpacking" not in prompt and "拆包" not in prompt
    assert "container_number（柜号）按用户原文提取" in prompt and "unpacking_fee（拆柜费）只能是用户写出的阿拉伯数字" in prompt
