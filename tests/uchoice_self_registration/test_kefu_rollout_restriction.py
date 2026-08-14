"""
Coverage for core.kefu_case_adapter._kefu_rollout_denial_reason: approved
customer-scoped services are enabled while
unrelated mutations remain gated.
"""
from types import SimpleNamespace

from core.kefu_case_adapter import _kefu_rollout_denial_reason

CONTEXT = {
    "allowed_services": [
        {"service_type_id": "svc-view-storage", "name": "view_storage"},
        {"service_type_id": "svc-inbound", "name": "uchoice_inbound_request"},
        {"service_type_id": "svc-role-change", "name": "role_change"},
        {"service_type_id": "svc-fedex-label", "name": "fedex_label"},
    ]
}


def _ai_response(service_type_name=None):
    return SimpleNamespace(service_type_name=service_type_name)


def test_new_request_for_read_only_service_is_allowed():
    assert _kefu_rollout_denial_reason(CONTEXT, _ai_response("view_storage"), None) is None


def test_new_request_for_kefu_native_customer_service_is_allowed():
    assert _kefu_rollout_denial_reason(
        CONTEXT, _ai_response("uchoice_inbound_request"), None
    ) is None


def test_new_request_for_role_change_is_allowed():
    """role_change graduated onto the Kefu allowlist -- it already has the
    full generic pipeline plus a Kefu-identity-aware handler, so unlike
    fedex_label there's no unverified external side effect blocking it."""
    assert _kefu_rollout_denial_reason(CONTEXT, _ai_response("role_change"), None) is None


def test_new_request_for_unimplemented_mutating_service_is_denied():
    reason = _kefu_rollout_denial_reason(CONTEXT, _ai_response("fedex_label"), None)
    assert reason == "service_not_enabled_for_kefu"


def test_new_request_with_no_service_named_is_allowed_through():
    """Unrecognized/check_services intents have no service to gate."""
    assert _kefu_rollout_denial_reason(CONTEXT, _ai_response(None), None) is None


def test_continuing_session_on_read_only_service_is_allowed():
    session = SimpleNamespace(service_type_id="svc-view-storage")
    assert _kefu_rollout_denial_reason(CONTEXT, _ai_response(None), session) is None


def test_continuing_session_on_mutating_service_is_denied():
    """Defense in depth -- a Kefu session should never point at a mutating
    service in practice (this same gate blocks it at creation), but the
    session-side check is defensive in case that invariant is ever broken."""
    session = SimpleNamespace(service_type_id="svc-fedex-label")
    reason = _kefu_rollout_denial_reason(CONTEXT, _ai_response(None), session)
    assert reason == "service_not_enabled_for_kefu"


def test_unknown_service_type_id_on_session_does_not_crash():
    session = SimpleNamespace(service_type_id="svc-unknown")
    assert _kefu_rollout_denial_reason(CONTEXT, _ai_response(None), session) is None
