import pytest

from clients.kefu_client import KefuAPIError, ServiceState
from core.kefu_case_adapter import _direct_send
from core.kefu_contracts import KefuIdentity


class FakeClient:
    def __init__(self, state, send_error=None, state_error=None):
        self.state = state
        self.send_error = send_error
        self.state_error = state_error
        self.send_calls = []

    def get_service_state(self, **kwargs):
        if self.state_error:
            raise self.state_error
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


def test_unsupported_state_preflight_does_not_block_authoritative_send(capsys):
    client = FakeClient(0, state_error=KefuAPIError(48002, "api forbidden"))

    _direct_send(client, KefuIdentity("kf", "customer"), "reply-1", "hello")

    assert len(client.send_calls) == 1
    assert "attempting send" in capsys.readouterr().out
