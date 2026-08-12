"""
check_services must only advertise what's actually invokable for Kefu --
_KEFU_CHECK_SERVICES_VISIBLE is derived from _KEFU_ENABLED_SERVICES (the
real rollout gate _kefu_rollout_denial_reason checks) plus the one
deliberate exception (purge_kefu_sessions, invokable via its own separate
pre-AI command despite being excluded from the rollout allowlist).

adjust_storage/recount_storage/move_storage were enabled for Kefu once
their existing generic wiring (PRE_CONFIRM_VALIDATORS, CONFIRMATION_
BUILDERS, HANDLER_REGISTRY dispatch through the same advisory-lock-
protected apply_storage_delta) was verified end-to-end via the Kefu-native
path -- see tests/kefu_integration/test_kefu_storage_correction_services.py.
"""
from core.kefu_case_adapter import _KEFU_CHECK_SERVICES_VISIBLE, _KEFU_ENABLED_SERVICES


def test_storage_correction_services_are_enabled_and_visible():
    for name in ("adjust_storage", "recount_storage", "move_storage"):
        assert name in _KEFU_ENABLED_SERVICES
        assert name in _KEFU_CHECK_SERVICES_VISIBLE


def test_purge_kefu_sessions_stays_visible_despite_being_outside_the_rollout_allowlist():
    assert "purge_kefu_sessions" not in _KEFU_ENABLED_SERVICES
    assert "purge_kefu_sessions" in _KEFU_CHECK_SERVICES_VISIBLE


def test_every_rollout_enabled_service_is_check_services_visible():
    assert _KEFU_ENABLED_SERVICES <= _KEFU_CHECK_SERVICES_VISIBLE
