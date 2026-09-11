"""
fedex_label/ups_label pre-confirm validation: shipper_phone/recipient_phone
must contain at least 10 digits. Pure function, no DB access, so this runs
offline (db=None is safe -- the validator never touches it).
"""
import pytest

from core import pre_confirm_validators


@pytest.mark.parametrize("service_name", ["fedex_label", "ups_label"])
@pytest.mark.parametrize("field_name", ["shipper_phone", "recipient_phone"])
def test_rejects_too_few_digits(service_name, field_name):
    error = pre_confirm_validators.run(service_name, {}, {field_name: "12345"}, None)
    assert error is not None
    assert "10位数字" in error


@pytest.mark.parametrize("service_name", ["fedex_label", "ups_label"])
def test_accepts_ten_digit_number(service_name):
    error = pre_confirm_validators.run(
        service_name, {}, {"shipper_phone": "1234567890", "recipient_phone": "1234567890"}, None,
    )
    assert error is None


@pytest.mark.parametrize("service_name", ["fedex_label", "ups_label"])
def test_accepts_formatted_number_with_ten_digits(service_name):
    """Dashes/parentheses/leading +1 must not cause a false rejection --
    only the digit count matters."""
    error = pre_confirm_validators.run(
        service_name, {}, {"shipper_phone": "+1 (123) 456-7890", "recipient_phone": "123-456-7890"}, None,
    )
    assert error is None


@pytest.mark.parametrize("service_name", ["fedex_label", "ups_label"])
def test_missing_phone_field_not_rejected_here(service_name):
    """Required-field presence is enforced elsewhere (input_schema) -- this
    validator only checks digit count when a value IS present."""
    error = pre_confirm_validators.run(service_name, {}, {}, None)
    assert error is None
