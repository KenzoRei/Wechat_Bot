from __future__ import annotations

import requests

from clients.kefu_client import KefuAPIError, KefuQuotaExceeded, KefuWindowClosed
from core.kefu_contracts import Artifact, KefuIdentity
from core.kefu_delivery import (
    Failed,
    FilePayload,
    QuotaExceeded,
    Retryable,
    Sent,
    TextPayload,
    WindowClosed,
    content_hash,
    send_reply,
)


IDENTITY = KefuIdentity("kf-1", "staff-1")


class FakeClient:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def _fail(self):
        if self.failure:
            raise self.failure

    def send_text(self, **kwargs):
        self.calls.append(("text", kwargs))
        self._fail()
        return "provider-text"

    def upload_file(self, **kwargs):
        self.calls.append(("upload", kwargs))
        self._fail()
        return "media-1"

    def send_file(self, **kwargs):
        self.calls.append(("file", kwargs))
        self._fail()
        return "provider-file"


def test_text_delivery_uses_stable_delivery_key():
    client = FakeClient()
    result = send_reply(
        client,
        recipient=IDENTITY,
        delivery_key="delivery-1",
        payload=TextPayload("copy-ready"),
    )
    assert result == Sent("provider-text")
    assert client.calls[0][1]["msgid"] == "delivery-1"


def test_file_hash_is_checked_before_upload():
    artifact = Artifact(b"pdf", "pickup.pdf", "application/pdf", "artifact-1")
    client = FakeClient()
    result = send_reply(
        client,
        recipient=IDENTITY,
        delivery_key="delivery-1",
        payload=FilePayload(artifact, expected_hash=content_hash(b"different")),
    )
    assert result == Failed("artifact_hash_mismatch")
    assert client.calls == []


def test_valid_file_uploads_then_sends():
    artifact = Artifact(b"pdf", "pickup.pdf", "application/pdf", "artifact-1")
    client = FakeClient()
    result = send_reply(
        client,
        recipient=IDENTITY,
        delivery_key="delivery-1",
        payload=FilePayload(artifact, expected_hash=content_hash(b"pdf")),
    )
    assert result == Sent("provider-file")
    assert [kind for kind, _ in client.calls] == ["upload", "file"]
    assert client.calls[1][1]["media_id"] == "media-1"


def test_claude_artifact_mapping_matches_transport_boundary():
    from core.kefu_contracts import coerce_artifact

    artifact = coerce_artifact(
        {
            "bytes": b"pdf",
            "filename": "pickup.pdf",
            "content_type": "application/pdf",
            "artifact_key": "request-1:outbound_instruction",
        }
    )
    assert artifact.content == b"pdf"
    assert artifact.artifact_key == "request-1:outbound_instruction"


def test_provider_failures_map_to_signed_transport_contract():
    cases = [
        (KefuWindowClosed(95002, "closed"), WindowClosed),
        (KefuQuotaExceeded(95001, "quota"), QuotaExceeded),
        (requests.Timeout("slow"), Retryable),
        (KefuAPIError(40058, "invalid"), Failed),
    ]
    for error, result_type in cases:
        result = send_reply(
            FakeClient(error),
            recipient=IDENTITY,
            delivery_key="delivery-1",
            payload=TextPayload("copy-ready"),
        )
        assert isinstance(result, result_type)
