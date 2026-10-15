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
def test_sanitizer_abbreviates_label_states(service):
    out = sanitize_extracted_fields_before_persistence(
        service, {"shipper_state": "Delaware", "recipient_state": "Texas"}, db=None)
    assert out == {"shipper_state": "DE", "recipient_state": "TX"}


def test_validator_rejects_unknown_us_state_but_skips_non_us():
    assert _valid_label_states({}, {"recipient_state": "Atlantis"}, None)
    assert _valid_label_states({}, {"shipper_state": "California"}, None) is None
    assert _valid_label_states({}, {"recipient_state": "ON", "recipient_country": "CA"}, None) is None
