from datetime import datetime, timezone

import pytest

from clients.kefu_client import SyncPage
from core.kefu_sync import (
    CLAIM_SQL,
    extract_case_number_hint,
    is_customer_message,
    log_provider_event,
    normalize_message,
    sync_available_messages,
)


def test_normalize_requires_transport_identity_and_message_type():
    message = normalize_message(
        {"msgid": 1, "open_kfid": "kf", "external_userid": "staff", "msgtype": "text"}
    )
    assert message["msgid"] == "1"
    with pytest.raises(ValueError, match="external_userid"):
        normalize_message({"msgid": "1", "open_kfid": "kf", "msgtype": "text"})


def test_only_customer_origin_messages_reach_business_processing():
    assert is_customer_message(
        {
            "msgid": "customer-1",
            "open_kfid": "kf",
            "external_userid": "external",
            "origin": 3,
            "msgtype": "text",
        }
    ) is True
    assert is_customer_message(
        {
            "msgid": "event-1",
            "origin": 4,
            "msgtype": "event",
            "event": {
                "event_type": "enter_session",
                "open_kfid": "kf",
                "external_userid": "external",
            },
        }
    ) is False
    assert is_customer_message(
        {
            "msgid": "servicer-1",
            "open_kfid": "kf",
            "external_userid": "external",
            "origin": 5,
            "msgtype": "text",
        }
    ) is False


def test_send_failure_event_is_visible_in_logs(capsys):
    log_provider_event(
        {
            "msgtype": "event",
            "event": {
                "event_type": "msg_send_fail",
                "fail_msgid": "outbound-1",
                "fail_type": 2,
            },
        }
    )
    output = capsys.readouterr().out
    assert "fail_msgid=outbound-1" in output
    assert "fail_type=2" in output


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("继续 CASE-20260811-000123 的操作", "CASE-20260811-000123"),
        ("case-20260811-000123", "CASE-20260811-000123"),
        ("CASE-20260811-12345", None),
        ("XCASE-20260811-000123", None),
        ("没有案件号", None),
        (None, None),
    ],
)
def test_case_number_hint_is_only_an_explicit_full_identifier(content, expected):
    assert extract_case_number_hint(content) == expected


def test_claim_sql_claims_only_the_oldest_outstanding_row_when_due():
    """Audio-input plan D7: an identity's messages are strictly ordered.
    Only its oldest outstanding (pending/claimed) row is eligible -- which
    also means nothing is claimable while that row holds a live lease (the
    old NOT EXISTS guard's job) -- and only when due. Behavior is covered
    against PostgreSQL in tests/kefu_integration/test_kefu_voice.py."""
    sql = " ".join(str(CLAIM_SQL).lower().split())
    assert "oldest.status in ('pending', 'claimed')" in sql
    assert "order by oldest.received_at, oldest.msgid" in sql
    assert "q.next_attempt_at is null or q.next_attempt_at <= now()" in sql
    assert "q.status = 'claimed' and q.lease_expires_at < now()" in sql
    # SKIP LOCKED would let a newer message jump past a momentarily locked
    # oldest row; claimers are serialized per identity by claim_next's
    # advisory lock instead.
    assert "skip locked" not in sql


class FakeCursor:
    def __init__(self, cursor):
        self.cursor = cursor


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, cursor="same"):
        self.cursor = cursor
        self.executed = []
        self.closed = False

    def begin(self):
        return FakeTransaction()

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))

    def get(self, model, key):
        return FakeCursor(self.cursor)

    def close(self):
        self.closed = True


class StalledClient:
    def sync_messages(self, **kwargs):
        return SyncPage(messages=[], next_cursor=kwargs["cursor"], has_more=True)


def test_sync_stops_on_nonadvancing_cursor_before_ingestion():
    db = FakeDB()
    with pytest.raises(RuntimeError, match="did not advance"):
        sync_available_messages(lambda: db, StalledClient(), sync_token="token", open_kfid="kf")
    assert "pg_advisory_xact_lock" in db.executed[0][0]
    assert db.closed is True
