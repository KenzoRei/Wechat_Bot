"""
Suggested charge type for a new address (address-pivot plan rev 3, item 2),
shared by Kefu (core/kefu_turn_apply.py) and Smart Robot
(core/workflow_engine.py).

The AI only ever supplies an estimated drive time in minutes, from the
origin warehouse to the recipient; code alone maps minutes to a tier, so
the price shown can never disagree with the minutes. A charge type the user
states always wins and is never replaced by an estimate.

Address completeness: the AI understands the address and returns it split
into parts (addr_parts: street, optional unit, city, state, zip). Code
checks each part and, when all pass, builds the address string itself in
one standard format ("182-08 149th Avenue, Springfield Gardens, NY 11413").
That built string is what's stored and shown in the confirmation, so a
misreading is visible before 确认. Only a verified address gets a
suggestion; anything else keeps the AI's own addr text and the charge type
is asked for, as before.

Suggestion state lives next to charge_type/addr in the upsert_address
session's collected_fields, under keys the address handler never copies:
  _charge_type_suggested         True when the system (not the user) set charge_type
  _charge_type_estimate_minutes  the minutes behind that suggestion
  _addr_verified                 the addr string code built from valid parts;
                                 addr is verified only while it still equals it
"""
import re

from core.uchoice_rates import CHARGE_TYPE_RATES

SUGGESTED_KEY = "_charge_type_suggested"
MINUTES_KEY = "_charge_type_estimate_minutes"
VERIFIED_KEY = "_addr_verified"

MIN_MINUTES = 1
MAX_MINUTES = 600

# Drive-time tiers (core/uchoice_rates.CHARGE_TYPE_DESCRIPTIONS): under 5
# minutes short delivery, 5 to 20 inclusive delivery, over 20 truck transfer.
_SHORT_DELIVERY_BELOW = 5
_DELIVERY_UP_TO = 20

# USPS state codes, plus DC and PR.
US_STATES = frozenset((
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR"
).split())
_ZIP = re.compile(r"^\d{5}(-\d{4})?$")
_STREET = re.compile(r"^\d[\w-]*\s+\S")   # house number, then the street name


def valid_minutes(value) -> int | None:
    """An int (not bool) from 1 to 600, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_MINUTES <= value <= MAX_MINUTES else None


def valid_charge_type(value) -> str | None:
    return value if isinstance(value, str) and value in CHARGE_TYPE_RATES else None


def _part(parts: dict, key: str) -> str:
    value = parts.get(key)
    return " ".join(value.split()) if isinstance(value, str) else ""


def build_address(parts) -> str | None:
    """
    The AI's addr_parts, checked part by part; the standard address string
    when every required part is valid, else None:
      street  starts with a house number ("182-08 149th Avenue")
      unit    optional ("Suite 2")
      city    non-empty, contains a letter
      state   a two-letter US state code (any case)
      zip     5 digits, or ZIP+4
    """
    if not isinstance(parts, dict):
        return None
    street, unit, city = _part(parts, "street"), _part(parts, "unit"), _part(parts, "city")
    state, zip_code = _part(parts, "state").upper(), _part(parts, "zip")
    if not _STREET.match(street) or not re.search(r"[A-Za-z]", city):
        return None
    if state not in US_STATES or not _ZIP.match(zip_code):
        return None
    return ", ".join(p for p in (street, unit, city, f"{state} {zip_code}") if p)


def _with_verified_addr(fields: dict, parts) -> dict:
    built = build_address(parts)
    return {**fields, "addr": built, VERIFIED_KEY: built} if built else fields


def _can_suggest(fields: dict) -> bool:
    """Both ends of the drive are known, and this is a new address: the
    addr is still the one code built from valid parts, and an update
    (matched_address_id) keeps the stored charge type unless the user
    states a new one."""
    addr = fields.get("addr")
    return (
        bool(addr) and addr == fields.get(VERIFIED_KEY)
        and bool(fields.get("warehouse_code"))
        and not fields.get("matched_address_id")
    )


def tier_for_minutes(minutes: int) -> str:
    if minutes < _SHORT_DELIVERY_BELOW:
        return "short_delivery"
    if minutes <= _DELIVERY_UP_TO:
        return "delivery"
    return "truck_transfer"


def sanitize_new_address(raw) -> dict | None:
    """
    The AI's best-effort new destination from an unmatched outbound
    (Kefu address_match.new_address, Smart Robot unmatched_new_address).
    Keeps non-empty company_name/addr, a valid estimated_drive_minutes, and
    a valid charge_type (only ever filled from the user's own words). Valid
    addr_parts replace addr with the address built from them, marked
    verified. Everything else is dropped.
    """
    if not isinstance(raw, dict):
        return None
    sanitized = {}
    for key in ("company_name", "addr"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            sanitized[key] = value.strip()
    sanitized = _with_verified_addr(sanitized, raw.get("addr_parts"))
    minutes = valid_minutes(raw.get("estimated_drive_minutes"))
    if minutes is not None:
        sanitized["estimated_drive_minutes"] = minutes
    charge_type = valid_charge_type(raw.get("charge_type"))
    if charge_type is not None:
        sanitized["charge_type"] = charge_type
    return sanitized or None


def seed_fields(new_address: dict) -> dict:
    """The address fields a handoff seeds from a sanitized new address."""
    return {k: v for k, v in (new_address or {}).items() if k in ("company_name", "addr", VERIFIED_KEY) and v}


def _suggest(fields: dict, minutes: int) -> dict:
    return {**fields, "charge_type": tier_for_minutes(minutes), SUGGESTED_KEY: True, MINUTES_KEY: minutes}


def _clear_suggestion(fields: dict, *, drop_charge_type: bool) -> dict:
    cleared = {k: v for k, v in fields.items() if k not in (SUGGESTED_KEY, MINUTES_KEY)}
    if drop_charge_type:
        cleared.pop("charge_type", None)
    return cleared


def seed_charge_type(seed: dict, new_address: dict) -> dict:
    """Handoff: a stated charge type wins over a simultaneous estimate."""
    stated = valid_charge_type(new_address.get("charge_type"))
    if stated:
        return {**seed, "charge_type": stated}
    minutes = valid_minutes(new_address.get("estimated_drive_minutes"))
    if minutes is not None and _can_suggest(seed):
        return _suggest(seed, minutes)
    return seed


def apply_address_turn(
    previous: dict, merged: dict, extracted: dict, raw_minutes,
    stated_flag: bool = False, raw_parts=None,
) -> dict:
    """
    One upsert_address turn, after this turn's extracted fields were merged
    over `previous` into `merged`.
    - Valid addr_parts set addr to the address code builds from them.
    - The user stated a charge type: it stands, the suggestion is cleared.
      An extracted charge_type that differs from the current suggestion can
      only be a statement. One equal to it counts only with the AI's explicit
      stated_flag (charge_type_stated) -- otherwise it is the AI repeating
      the collected value.
    - An update of an existing address (matched_address_id) never gets a
      suggestion; one already made is dropped.
    - Otherwise a valid estimate for a verified address (re)sets a suggested
      charge type, but never overrides one the user stated earlier.
    - Otherwise, if addr or warehouse_code changed, a suggested charge type
      no longer applies: it is dropped, so the user is asked.
    """
    built = build_address(raw_parts)
    if built:
        merged = {**merged, "addr": built, VERIFIED_KEY: built}
        extracted = {**extracted, "addr": built}

    was_suggested = bool(previous.get(SUGGESTED_KEY))
    stated = valid_charge_type(extracted.get("charge_type"))
    if stated and (stated_flag or not was_suggested or stated != previous.get("charge_type")):
        return {**_clear_suggestion(merged, drop_charge_type=False), "charge_type": stated}

    if merged.get("matched_address_id"):
        return _clear_suggestion(merged, drop_charge_type=was_suggested)
    user_stated_before = bool(previous.get("charge_type")) and not was_suggested
    if user_stated_before:
        return merged
    minutes = valid_minutes(raw_minutes)
    if minutes is not None and _can_suggest(merged):
        return _suggest(merged, minutes)
    location_changed = any(
        key in extracted and extracted[key] != previous.get(key) for key in ("addr", "warehouse_code")
    )
    if was_suggested and location_changed:
        return _clear_suggestion(merged, drop_charge_type=True)
    return merged


def suggestion_note(fields: dict) -> str | None:
    """The confirmation's explanation for a suggested charge type."""
    if not fields.get(SUGGESTED_KEY):
        return None
    minutes = fields.get(MINUTES_KEY)
    warehouse = fields.get("warehouse_code")
    origin = f"{warehouse} 仓出发，" if warehouse else ""
    return f"预计车程约 {minutes} 分钟（{origin}系统估算，如有误请直接说明）"


def origin_warehouse_addresses(db) -> list[dict]:
    """Origin addresses the AI estimates from: our own inventory warehouses
    (JFK/DE/NJ) as recorded in the warehouse directory. A warehouse missing
    from the directory simply gets no estimate."""
    from core.uchoice_constants import VALID_WAREHOUSE_CODES
    from core.warehouse_directory import list_warehouses

    return [
        {"warehouse_code": w.warehouse_abbr, "address": f"{w.addr}, {w.city}, {w.state} {w.zip_code}"}
        for w in list_warehouses(db)
        if w.warehouse_abbr in VALID_WAREHOUSE_CODES
    ]
