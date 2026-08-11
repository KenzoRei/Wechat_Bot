import pytest

from clients.kefu_client import KefuAPIError, ServiceState
from core.kefu_case_adapter import _direct_send
from core.kefu_contracts import KefuIdentity


class FakeClient:
    def __init__(self, state, send_error=None):
        self.state = state
        self.send_error = send_error
        self.send_calls = []

    def get_service_state(self, **kwargs):
        return ServiceState(self.state, "human-1" if self.state == 3 else None)

    def send_text(self, **kwargs):
        self.send_calls.append(kwargs)
        if self.send_error:
            raise self.send_error
        return kwargs["msgid"]


def test_direct_send_rejects_human_reception_state_before_send():
    client = FakeClient(3)
    with pytest.raises(RuntimeError, match="service_state=3"):
        _direct_send(client, KefuIdentity("kf", "customer"), "reply-1", "hello")
    assert client.send_calls == []


def test_direct_send_surfaces_provider_failure():
    client = FakeClient(0, KefuAPIError(40058, "invalid msgid"))
    with pytest.raises(RuntimeError, match="40058"):
        _direct_send(client, KefuIdentity("kf", "customer"), "reply-1", "hello")
