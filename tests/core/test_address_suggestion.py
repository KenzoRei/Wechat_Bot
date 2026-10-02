"""
Suggested charge type for a new address (core/address_suggestion.py,
address-pivot plan rev 3, item 2): the AI gives only minutes and its reading
of the address as parts; code checks the parts, builds the address, and
picks the tier. A stated charge type always wins. Offline.
"""
import pytest

from core import address_suggestion as s
from core.confirmation import build_confirmation_sections

SUGGESTED = {s.SUGGESTED_KEY: True}
FULL = "1 Main St, Jamaica, NY 11434"
PARTS = {"street": "1 Main St", "city": "Jamaica", "state": "NY", "zip": "11434"}
# A verified address: addr equals the string code built from valid parts.
BASE = {"addr": FULL, s.VERIFIED_KEY: FULL, "warehouse_code": "JFK"}


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


# ── Address parts ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("parts, built", [
    (PARTS, FULL),
    ({"street": "182-08 149th Avenue", "city": "Springfield Gardens", "state": "ny", "zip": "11413"},
     "182-08 149th Avenue, Springfield Gardens, NY 11413"),
    ({"street": "600  Blair Rd", "unit": "Suite 2", "city": "Carteret", "state": "NJ", "zip": "07008-1234"},
     "600 Blair Rd, Suite 2, Carteret, NJ 07008-1234"),
])
def test_valid_parts_build_the_standard_address(parts, built):
    assert s.build_address(parts) == built


@pytest.mark.parametrize("change", [
    {"zip": None}, {"zip": "1143"}, {"zip": "ABCDE"},
    {"state": "New York"}, {"state": "XX"}, {"state": None},
    {"city": ""}, {"city": "123"},
    {"street": "Main St"}, {"street": "12"}, {"street": None},
])
def test_missing_or_invalid_parts_build_nothing(change):
    parts = {k: v for k, v in {**PARTS, **change}.items() if v is not None}
    assert s.build_address(parts) is None


@pytest.mark.parametrize("raw", [None, "1 Main St, Jamaica, NY 11434", ["1 Main St"]])
def test_non_dict_parts_build_nothing(raw):
    assert s.build_address(raw) is None


# ── Handoff sanitizing and seeding ───────────────────────────────────────────

def test_sanitize_new_address_keeps_valid_keys_and_builds_addr_from_parts():
    raw = {"company_name": " ABC ", "addr": "1 main st jamaica ny 11434", "addr_parts": PARTS,
           "estimated_drive_minutes": 12, "charge_type": "delivery", "note": "x", "address_id": "u"}
    assert s.sanitize_new_address(raw) == {
        "company_name": "ABC", "addr": FULL, s.VERIFIED_KEY: FULL,
        "estimated_drive_minutes": 12, "charge_type": "delivery",
    }


def test_sanitize_new_address_keeps_ai_addr_when_parts_are_invalid():
    raw = {"addr": "1 Main St, Jamaica, NY", "addr_parts": {**PARTS, "zip": ""}}
    assert s.sanitize_new_address(raw) == {"addr": "1 Main St, Jamaica, NY"}


@pytest.mark.parametrize("bad", [
    {"estimated_drive_minutes": True}, {"estimated_drive_minutes": 0}, {"estimated_drive_minutes": 601},
    {"estimated_drive_minutes": "12"}, {"charge_type": "express"}, {"charge_type": ""},
])
def test_sanitize_new_address_drops_invalid_values(bad):
    assert s.sanitize_new_address({"addr": "1 Main St", **bad}) == {"addr": "1 Main St"}


def test_seed_fields_carry_the_verified_marker():
    sanitized = s.sanitize_new_address({"company_name": "ABC", "addr_parts": PARTS, "estimated_drive_minutes": 9})
    assert s.seed_fields(sanitized) == {"company_name": "ABC", "addr": FULL, s.VERIFIED_KEY: FULL}


def test_seed_suggests_a_tier_for_a_verified_address():
    seed = s.seed_charge_type(dict(BASE), {"estimated_drive_minutes": 12})
    assert seed == {**BASE, "charge_type": "delivery", s.SUGGESTED_KEY: True, s.MINUTES_KEY: 12}


def test_seed_with_unverified_address_makes_no_suggestion():
    seed = {"addr": FULL, "warehouse_code": "JFK"}       # AI text only, no valid parts
    assert s.seed_charge_type(dict(seed), {"estimated_drive_minutes": 12}) == seed


def test_seed_stated_charge_type_beats_a_simultaneous_estimate():
    seed = s.seed_charge_type(dict(BASE), {"charge_type": "self_pickup", "estimated_drive_minutes": 12})
    assert seed == {**BASE, "charge_type": "self_pickup"}


def test_seed_without_estimate_or_statement_leaves_charge_type_to_ask():
    assert s.seed_charge_type(dict(BASE), {}) == BASE


def test_seed_without_origin_warehouse_makes_no_suggestion():
    seed = {"addr": FULL, s.VERIFIED_KEY: FULL}
    assert s.seed_charge_type(dict(seed), {"estimated_drive_minutes": 12}) == seed


# ── Later address turns ──────────────────────────────────────────────────────

def test_turn_estimate_sets_a_suggestion():
    out = s.apply_address_turn(BASE, dict(BASE), {}, 3)
    assert out["charge_type"] == "short_delivery" and out[s.SUGGESTED_KEY] and out[s.MINUTES_KEY] == 3


def test_turn_parts_verify_and_rewrite_a_messy_address():
    messy = {"addr": "1 main st jamaica ny11434", "warehouse_code": "JFK"}
    out = s.apply_address_turn({}, dict(messy), {"addr": messy["addr"]}, 12, raw_parts=PARTS)
    assert out == {**BASE, "charge_type": "delivery", s.SUGGESTED_KEY: True, s.MINUTES_KEY: 12}


def test_turn_addr_changed_without_parts_is_no_longer_verified():
    prev = {**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12}
    merged = {**prev, "addr": "9 Other St, Jamaica, NY 11434"}
    out = s.apply_address_turn(prev, merged, {"addr": merged["addr"]}, 15)
    assert "charge_type" not in out and s.SUGGESTED_KEY not in out


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
    other = {"street": "9 Other St", "city": "Jamaica", "state": "NY", "zip": "11434"}
    moved = s.apply_address_turn(out, dict(out), {}, 30, raw_parts=other)
    assert moved["charge_type"] == "delivery" and s.SUGGESTED_KEY not in moved


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


def test_turn_address_change_keeps_a_stated_charge_type():
    prev = {**BASE, "charge_type": "delivery"}
    merged = {**prev, "addr": "9 Other St, Jamaica, NY 11434"}
    assert s.apply_address_turn(prev, merged, {"addr": merged["addr"]}, None) == merged


@pytest.mark.parametrize("fields, parts", [
    ({"addr": FULL, s.VERIFIED_KEY: FULL}, None),                    # no warehouse
    ({"addr": "1 Main St", "warehouse_code": "JFK"}, None),          # unverified
    ({"addr": "1 Main St", "warehouse_code": "JFK"}, {**PARTS, "zip": ""}),  # invalid parts
])
def test_turn_without_a_verified_location_makes_no_suggestion(fields, parts):
    """Audit #4: no warehouse, or an address not verified from parts -> no tier."""
    assert "charge_type" not in s.apply_address_turn({}, dict(fields), dict(fields), 12, raw_parts=parts)


# ── Confirmation display ─────────────────────────────────────────────────────

def _address_items(fields):
    return build_confirmation_sections("upsert_address", fields, None)[0]["items"]


def test_confirmation_shows_the_suggestion_and_hides_internal_keys():
    items = _address_items({**BASE, "charge_type": "delivery", **SUGGESTED, s.MINUTES_KEY: 12,
                            "_resume_outbound_session_id": "x"})
    assert items["计费类型"] == "配送（$45） — 预计车程约 12 分钟（JFK 仓出发，系统估算，如有误请直接说明）"
    assert items["地址"] == FULL
    assert not any(str(v) == "x" or str(k).startswith("_") for k, v in items.items())


def test_confirmation_stated_charge_type_has_no_estimate_line():
    assert _address_items({**BASE, "charge_type": "delivery"})["计费类型"] == "配送（$45）"
