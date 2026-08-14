"""Offline isolation for the Phase 4 self-registration suite -- same pattern
as tests/uchoice_lifecycle/conftest.py (mock DB, no real Postgres/SQLite
connection), respecting the current DB-test-policy restriction pending the
user's dedicated test database."""

import os

import pytest

_REQUIRED_TEST_ENV = {
    "WECHAT_CORP_ID": "offline-test",
    "WECHAT_TOKEN": "offline-test",
    "WECHAT_ENCODING_AES_KEY": "offline-test",
    "YIDIDA_BASE_URL": "https://blocked.invalid",
    "OMS_BASE_URL": "https://blocked.invalid",
    "CLAUDE_API_KEY": "offline-test",
    "OPENAI_API_KEY": "offline-test",
    "ADMIN_API_KEY": "offline-test",
    "DATABASE_URL": "sqlite:///:memory:",
}

for _name, _value in _REQUIRED_TEST_ENV.items():
    os.environ.setdefault(_name, _value)


@pytest.fixture(autouse=True)
def block_operational_clients(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("operational client call attempted in offline test")

    monkeypatch.setattr("clients.wechat_client.send_message", blocked)
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_message", blocked)
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_file", blocked)
    monkeypatch.setattr("clients.oms_client.query_outbound_order", blocked)
    monkeypatch.setattr("clients.oms_client.create_work_order", blocked)
    monkeypatch.setattr("clients.yidida_client.create_label", blocked)
