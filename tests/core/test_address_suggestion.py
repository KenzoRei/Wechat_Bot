"""
Suggested charge type for a new address (core/address_suggestion.py,
address-pivot plan rev 3, item 2): the AI gives only minutes, code picks the
tier, a stated charge type always wins. Offline.
"""
import pytest

from core import address_suggestion as s
from core.confirmation import build_confirmation_sections

SUGGESTED = {s.SUGGESTED_KEY: True}


@pytest.mark.parametrize("minutes, tier", [
    (1, "short_delivery"), (4, "short_delivery"),
    (5, "delivery"), (12, "delivery"), (20, "delivery"),
    (21, "truck_transfer"), (600, "truck_transfer"),
])
def test_minutes_map_to_tiers_at_the_boundaries(minutes, tier):
    assert s.tier_for_minutes(minutes) == tier


@pytest.mark.parametrize("value", [None, True, False, 0, 601, -3, "12", 12.5])
def test_invalid_minutes_are_rejected(value):
    assert s.valid_minutes(value) is None


def test_sanitize_new_address_keeps_valid_keys_only():
    raw = {"company_name": " ABC ", "addr": "1 Main St, Jamaica, NY 11434",
           "estimated_drive_minutes": 12, "charge_type": "delivery", "note": "x", "address_id": "u"}
    assert s.sanitize_new_address(raw) == {
        "company_name": "ABC", "addr": "1 Main St, Jamaica, NY 11434",
        "estimated_drive_minutes": 12, "charge_type": "delivery",
    }


@pytest.mark.parametrize("bad", [
    {"estimated_drive_minutes": True}, {"estimated_drive_minutes": 0}, {"estimated_drive_minutes": 601},
    {"estimated_drive_minutes": "12"}, {"charge_type": "express"}, {"charge_type": ""},
])
def test_sanitize_new_address_drops_invalid_values(bad):
    assert s.sanitize_new_address({"addr": "1 Main St", **bad}) == {"addr": "1 Main St"}


FULL = "1 Main St, Jamaica, NY 11434"


def test_seed_suggests_a_tier_from_the_estimate():
    seed = s.seed_charge_type({"addr": FULL, "warehouse_code": "JFK"}, {"estimated_drive_minutes": 12})
    assert seed == {"addr": FULL, "warehouse_code": "JFK", "charge_type": "delivery",
                    s.SUGGESTED_KEY: True, s.MINUTES_KEY: 12}


def test_seed_with_incomplete_address_makes_no_suggestion():
    assert s.seed_charge_type({"addr": "1 Main St", "warehouse_code": "JFK"}, {"estimated_drive_minutes": 12})         == {"addr": "1 Main St", "warehouse_code": "JFK"}


@pytest.mark.parametrize("addr, complete", [
    ("1 Main St, Jamaica, NY 11434", True),
    ("182-08 149th Avenue, Springfield Gardens, NY 11413", True),
    ("600 Blair Rd, Suite 2, Carteret, NJ 07008-1234", True),
    ("1 Main St", False), ("Main St, Jamaica, NY 11434", False),
    ("1 Main St, Jamaica, NY", False), ("1 Main St, Jamaica, 11434", False), (None, False),
])
def test_address_completeness(addr, complete):
    assert s.address_complete(addr) is complete


def test_seed_stated_charge_type_beats_a_simultaneous_estimate():
    seed = s.seed_charge_type({"addr": "a"}, {"charge_type": "self_pickup", "estimated_drive_minutes": 12})
    assert seed == {"addr": "a", "charge_type": "self_pickup"}


def test_seed_without_estimate_or_statement_leaves_charge_type_to_ask():
    assert s.seed_charge_type({"addr": "a"}, {}) == {"addr": "a"}


def test_seed_without_origin_warehouse_makes_no_suggestion():
    assert s.seed_charge_type({"addr": "a"}, {"estimated_drive_minutes": 12}) == {"addr": "a"}


BASE = {"addr": "1 Main St, Jamaica, NY 11434", "warehouse_code": "JFK"}


def test_turn_estimate_sets_a_suggestion():
    out = s.apply_address_turn(BASE, dict(BASE), {}, 3)
    assert out["charge_type"] == "short_delivery" and out[s.SUGGESTED_KEY] and out[s.MINUTES_KEY] == 3


def test_turn_stated_charge_type_clears_the_suggestion():
    prev = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    out = s.apply_address_turn(prev, {**prev, "charge_type": "truck_transfer"}, {"charge_type": "truck_transfer"}, 12)
    assert out == {**BASE, "charge_type": "truck_transfer"}


def test_turn_echo_of_the_suggested_charge_type_is_not_a_statement():
    prev = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    out = s.apply_address_turn(prev, dict(prev), {"charge_type": "delivery"}, None)
    assert out[s.SUGGESTED_KEY] is True


def test_turn_stating_the_suggested_tier_itself_is_a_statement():
    """Audit #2: "配送" said by the user while 配送 was suggested must stick,
    so a later address change with a new estimate can't replace it."""
    prev = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    out = s.apply_address_turn(prev, dict(prev), {"charge_type": "delivery"}, None, stated_flag=True)
    assert out == {**BASE, "charge_type": "delivery"}
    moved = {**out, "addr": "9 Other St, Jamaica, NY 11434"}
    assert s.apply_address_turn(out, moved, {"addr": moved["addr"]}, 30) == moved


def test_update_of_an_existing_address_never_gets_a_suggestion():
    """Audit #1: the estimate must not overwrite a stored charge type."""
    update = {**BASE, "matched_address_id": "a-1"}
    assert s.apply_address_turn({}, dict(update), {}, 12) == update
    suggested = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    became_update = {**suggested, "matched_address_id": "a-1"}
    assert s.apply_address_turn(suggested, became_update, {"matched_address_id": "a-1"}, 12) == update


def test_update_keeps_a_charge_type_the_user_states():
    update = {**BASE, "matched_address_id": "a-1"}
    out = s.apply_address_turn(update, {**update, "charge_type": "self_pickup"}, {"charge_type": "self_pickup"}, 12)
    assert out == {**update, "charge_type": "self_pickup"}


def test_turn_estimate_never_overrides_an_earlier_stated_charge_type():
    prev = {**BASE, "charge_type": "self_pickup"}
    assert s.apply_address_turn(prev, dict(prev), {}, 30) == prev


def test_turn_address_change_without_new_estimate_drops_the_suggestion():
    prev = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    merged = {**prev, "addr": "9 Other St, Jamaica, NY 11434"}
    assert s.apply_address_turn(prev, merged, {"addr": merged["addr"]}, None) == {**BASE, "addr": merged["addr"]}


def test_turn_address_change_keeps_a_stated_charge_type():
    prev = {**BASE, "charge_type": "delivery"}
    merged = {**prev, "addr": "9 Other St"}
    assert s.apply_address_turn(prev, merged, {"addr": "9 Other St"}, None) == merged


@pytest.mark.parametrize("fields", [
    {"addr": FULL}, {"addr": "1 Main St", "warehouse_code": "JFK"},
])
def test_turn_without_a_complete_location_makes_no_suggestion(fields):
    """Audit #4: no warehouse, or an incomplete address -> no tier."""
    assert "charge_type" not in s.apply_address_turn({}, dict(fields), dict(fields), 12)


def _address_items(fields):
    return build_confirmation_sections("upsert_address", fields, None)[0]["items"]


def test_confirmation_shows_the_suggestion_and_hides_internal_keys():
    items = _address_items({**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12,
                            "_resume_outbound_session_id": "x"})
    assert items["计费类型"] == "配送（$45） — 预计车程约 12 分钟（JFK 仓出发，系统估算，如有误请直接说明）"
    assert not any(str(v) == "x" or str(k).startswith("_") for k, v in items.items())


def test_confirmation_stated_charge_type_has_no_estimate_line():
    assert _address_items({**BASE, "charge_type": "delivery"})["计费类型"] == "配送（$45）"
