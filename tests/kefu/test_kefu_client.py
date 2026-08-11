from __future__ import annotations
import pytest

from clients.kefu_client import (
    KefuClient,
    KefuQuotaExceeded,
    KefuWindowClosed,
    provider_msgid,
)


class FakeResponse:
    def __init__(self, payload, *, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, token_payloads, request_payloads):
        self.token_payloads = list(token_payloads)
        self.request_payloads = list(request_payloads)
        self.get_calls = []
        self.request_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(self.token_payloads.pop(0))

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return FakeResponse(self.request_payloads.pop(0))


def client(http):
    return KefuClient("corp", "secret", http=http, api_base="https://example.test")


def test_sync_uses_cached_token_and_preserves_cursor_contract():
    http = FakeHTTP(
        [{"access_token": "token-1", "expires_in": 7200}],
        [
            {"errcode": 0, "next_cursor": "c2", "has_more": 1, "msg_list": [{"msgid": "m1"}]},
            {"errcode": 0, "next_cursor": "c3", "has_more": 0, "msg_list": []},
        ],
    )
    api = client(http)

    first = api.sync_messages(sync_token="sync", cursor="c1", open_kfid="kf1")
    second = api.sync_messages(sync_token="sync", cursor="c2", open_kfid="kf1")

    assert len(http.get_calls) == 1
    assert first.next_cursor == "c2" and first.has_more is True
    assert second.next_cursor == "c3" and second.has_more is False
    assert http.request_calls[0][2]["json"] == {
        "cursor": "c1",
        "token": "sync",
        "limit": 1000,
        "voice_format": 0,
        "open_kfid": "kf1",
    }


def test_get_service_state_uses_customer_identity():
    http = FakeHTTP(
        [{"access_token": "token", "expires_in": 7200}],
        [{"errcode": 0, "service_state": 3, "servicer_userid": "human-1"}],
    )

    state = client(http).get_service_state(open_kfid="kf1", external_userid="customer1")

    assert state.state == 3
    assert state.servicer_userid == "human-1"
    assert http.request_calls[0][2]["json"] == {
        "open_kfid": "kf1",
        "external_userid": "customer1",
    }


def test_invalid_token_refreshes_once_and_replays_original_upload_params():
    http = FakeHTTP(
        [
            {"access_token": "expired-token", "expires_in": 7200},
            {"access_token": "fresh-token", "expires_in": 7200},
        ],
        [
            {"errcode": 42001, "errmsg": "expired"},
            {"errcode": 0, "media_id": "media-1"},
        ],
    )

    media_id = client(http).upload_file(
        filename="pickup.pdf",
        content=b"pdf",
        content_type="application/pdf",
    )

    assert media_id == "media-1"
    assert len(http.get_calls) == 2
    assert [call[2]["params"] for call in http.request_calls] == [
        {"type": "file", "access_token": "expired-token"},
        {"type": "file", "access_token": "fresh-token"},
    ]


@pytest.mark.parametrize(
    ("errcode", "exception_type"),
    [(95001, KefuQuotaExceeded), (95002, KefuWindowClosed), (95018, KefuWindowClosed)],
)
def test_delivery_constraints_have_typed_errors(errcode, exception_type):
    http = FakeHTTP(
        [{"access_token": "token", "expires_in": 7200}],
        [{"errcode": errcode, "errmsg": "not sendable"}],
    )

    with pytest.raises(exception_type) as caught:
        client(http).send_text(
            open_kfid="kf1",
            external_userid="staff1",
            text="copy-ready",
            msgid="delivery-1",
        )
    assert caught.value.errcode == errcode


def test_send_text_rejects_oversize_utf8_without_http_call():
    http = FakeHTTP([], [])
    with pytest.raises(ValueError, match="2048"):
        client(http).send_text(
            open_kfid="kf1",
            external_userid="staff1",
            text="界" * 683,
            msgid="delivery-1",
        )
    assert http.get_calls == []
    assert http.request_calls == []


def test_provider_msgid_hashes_long_logical_keys_deterministically():
    logical = "kefu-denied:A1cudurde2uNTnezv1gvhMmBNn"
    first = provider_msgid(logical)
    second = provider_msgid(logical)

    assert first == second
    assert len(first.encode("utf-8")) == 32
    assert provider_msgid("delivery-1") == "delivery-1"


def test_send_text_uses_bounded_provider_msgid():
    http = FakeHTTP(
        [{"access_token": "token", "expires_in": 7200}],
        [{"errcode": 0}],
    )
    logical = "kefu-denied:A1cudurde2uNTnezv1gvhMmBNn"

    returned = client(http).send_text(
        open_kfid="kf1",
        external_userid="customer1",
        text="register first",
        msgid=logical,
    )

    sent = http.request_calls[0][2]["json"]["msgid"]
    assert sent == provider_msgid(logical)
    assert returned == sent
