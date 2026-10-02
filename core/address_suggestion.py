"""
Suggested charge type for a new address (address-pivot plan rev 3, item 2),
shared by Kefu (core/kefu_turn_apply.py) and Smart Robot
(core/workflow_engine.py).

The AI only ever supplies an estimated drive time in minutes, from the
origin warehouse to the recipient; code alone maps minutes to a tier, so
the price shown can never disagree with the minutes. A charge type the user
states always wins and is never replaced by an estimate.

Suggestion state lives next to charge_type in the upsert_address session's
collected_fields, under keys the address handler never copies:
  _charge_type_suggested         True when the system (not the user) set charge_type
  _charge_type_estimate_minutes  the minutes behind that suggestion
"""
from core.uchoice_rates import CHARGE_TYPE_RATES

SUGGESTED_KEY = "_charge_type_suggested"
MINUTES_KEY = "_charge_type_estimate_minutes"

MIN_MINUTES = 1
MAX_MINUTES = 600

# Drive-time tiers (core/uchoice_rates.CHARGE_TYPE_DESCRIPTIONS): under 5
# minutes short delivery, 5 to 20 inclusive delivery, over 20 truck transfer.
_SHORT_DELIVERY_BELOW = 5
_DELIVERY_UP_TO = 20


def valid_minutes(value) -> int | None:
    """An int (not bool) from 1 to 600, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_MINUTES <= value <= MAX_MINUTES else None


def valid_charge_type(value) -> str | None:
    return value if isinstance(value, str) and value in CHARGE_TYPE_RATES else None


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
    a valid charge_type (only ever filled from the user's own words);
    everything else is dropped.
    """
    if not isinstance(raw, dict):
        return None
    sanitized = {}
    for key in ("company_name", "addr"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            sanitized[key] = value.strip()
    minutes = valid_minutes(raw.get("estimated_drive_minutes"))
    if minutes is not None:
        sanitized["estimated_drive_minutes"] = minutes
    charge_type = valid_charge_type(raw.get("charge_type"))
    if charge_type is not None:
        sanitized["charge_type"] = charge_type
    return sanitized or None


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
    # An estimate needs both ends: no origin warehouse, no suggestion.
    if minutes is not None and seed.get("addr") and seed.get("warehouse_code"):
        return _suggest(seed, minutes)
    return seed


def apply_address_turn(previous: dict, merged: dict, extracted: dict, raw_minutes) -> dict:
    """
    One upsert_address turn, after this turn's extracted fields were merged
    over `previous` into `merged`.
    - The user stated a charge type: it stands, the suggestion is cleared.
      (An extracted charge_type that merely repeats the current suggestion
      is the AI echoing collected fields, not a statement.)
    - Otherwise a valid estimate (re)sets a suggested charge type, but never
      overrides one the user stated earlier.
    - Otherwise, if addr or warehouse_code changed, a suggested charge type
      no longer applies: it is dropped, so the user is asked.
    """
    was_suggested = bool(previous.get(SUGGESTED_KEY))
    stated = valid_charge_type(extracted.get("charge_type"))
    if stated and not (was_suggested and stated == previous.get("charge_type")):
        return {**_clear_suggestion(merged, drop_charge_type=False), "charge_type": stated}

    user_stated_before = bool(previous.get("charge_type")) and not was_suggested
    if user_stated_before:
        return merged
    minutes = valid_minutes(raw_minutes)
    if minutes is not None and merged.get("addr") and merged.get("warehouse_code"):
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
