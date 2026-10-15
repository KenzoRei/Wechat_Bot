import pytest

from core.pre_confirm_validators import _valid_label_states
from core.uchoice_field_sanitization import sanitize_extracted_fields_before_persistence
from core.us_state import normalize_label_states, to_state_abbr


@pytest.mark.parametrize("raw, expected", [
    ("California", "CA"), ("  new   york ", "NY"), ("ca", "CA"), ("TX", "TX"),
    ("Washington D.C.", "DC"), ("District of Columbia", "DC"), ("Puerto Rico", "PR"),
    ("Atlantis", "Atlantis"), ("", ""), (None, None),
])
def test_to_state_abbr(raw, expected):
    assert to_state_abbr(raw) == expected


def test_normalize_label_states_only_touches_state_fields():
    out = normalize_label_states({"shipper_state": "Florida", "recipient_state": "nj", "recipient_city": "Newark"})
    assert out == {"shipper_state": "FL", "recipient_state": "NJ", "recipient_city": "Newark"}


def test_normalize_is_country_aware_per_side():
    out = normalize_label_states({
        "shipper_state": "Florida", "recipient_state": "Maryland", "recipient_country": "LR",
    })
    assert out == {"shipper_state": "FL", "recipient_state": "Maryland", "recipient_country": "LR"}


@pytest.mark.parametrize("bad", [["CA"], {"a": 1}, 5])
def test_validator_rejects_non_string_state_without_crashing(bad):
    assert _valid_label_states({}, {"recipient_state": bad}, None)


@pytest.mark.parametrize("service", ["fedex_label", "ups_label"])
def test_sanitizer_keeps_state_as_said_so_a_later_country_still_applies(service):
    stored = sanitize_extracted_fields_before_persistence(service, {"recipient_state": "Maryland"}, db=None)
    assert stored == {"recipient_state": "Maryland"}
    merged = {**stored, "recipient_country": "LR"}   # country arrives next turn
    assert normalize_label_states(merged)["recipient_state"] == "Maryland"


def test_confirmation_shows_abbreviated_state():
    from core.confirmation import _label_sections_builder
    sections = _label_sections_builder("fedex")({"recipient_state": "California"}, None)
    recipient = next(s for s in sections if s["label"] == "收件人")
    assert list(recipient["items"].values()) == ["CA"]


def test_validator_rejects_unknown_us_state_but_skips_non_us():
    assert _valid_label_states({}, {"recipient_state": "Atlantis"}, None)
    assert _valid_label_states({}, {"shipper_state": "California"}, None) is None
    assert _valid_label_states({}, {"recipient_state": "ON", "recipient_country": "CA"}, None) is None
