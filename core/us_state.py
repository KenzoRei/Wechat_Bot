"""
US state normalization for FedEx/UPS label addresses: the carrier API wants
the two-letter USPS code, but customers (and the model) write "California",
"calif.", "ca", and so on.
"""
import re

_NAME_TO_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
    "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC", "WASHINGTON D.C.": "DC", "PUERTO RICO": "PR",
}
US_STATE_CODES = frozenset(_NAME_TO_ABBR.values())

# Fields on a fedex_label / ups_label request that hold a state.
LABEL_STATE_FIELDS = ("shipper_state", "recipient_state")


def to_state_abbr(value):
    """The two-letter code for a US state given as a code (any case) or full
    name; anything unrecognized (or non-string) is returned unchanged."""
    if not isinstance(value, str):
        return value
    key = re.sub(r"\s+", " ", value.replace(".", "")).strip().upper()
    if key in US_STATE_CODES:
        return key
    return _NAME_TO_ABBR.get(key, value)


def is_us_country(value) -> bool:
    """True for a missing country (labels default to US) or a US spelling."""
    if value is None or value == "":
        return True
    return isinstance(value, str) and value.strip().upper() in ("US", "USA", "UNITED STATES")


def normalize_label_states(fields: dict) -> dict:
    """A copy of `fields` with shipper_state / recipient_state abbreviated,
    side by side only where that side's country is the US."""
    out = dict(fields)
    for name in LABEL_STATE_FIELDS:
        side = name.split("_", 1)[0]
        if name in out and is_us_country(out.get(f"{side}_country")):
            out[name] = to_state_abbr(out[name])
    return out
