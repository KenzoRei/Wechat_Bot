"""Shared configuration and safety guards for the complete test suite."""
import os
import pytest


# Application configuration is validated during import. Offline tests use
# inert values and an in-memory database, so they never depend on a developer's
# .env file. PostgreSQL tests must opt in through TEST_DATABASE_URL below.
_OFFLINE_ENV = {
    "WECHAT_CORP_ID": "offline-test",
    "WECHAT_TOKEN": "offline-test",
    "WECHAT_ENCODING_AES_KEY": "offline-test",
    "WECHAT_KEFU_TOKEN": "offline-test",
    "WECHAT_KEFU_ENCODING_AES_KEY": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    "YIDIDA_BASE_URL": "https://blocked.invalid",
    "OMS_BASE_URL": "https://blocked.invalid",
    "CLAUDE_API_KEY": "offline-test",
    "OPENAI_API_KEY": "offline-test",
    "ADMIN_API_KEY": "offline-test",
    "DATABASE_URL": "sqlite:///:memory:",
}

_EXPLICIT_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

for _name, _value in _OFFLINE_ENV.items():
    os.environ.setdefault(_name, _value)

if _EXPLICIT_TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _EXPLICIT_TEST_DATABASE_URL


_PRODUCTION_DB_HOST_FRAGMENT = "dpg-d9p26h7lk1mc73a841d0-a.virginia-postgres.render.com"


def pytest_configure(config):
    config.addinivalue_line("markers", "postgres: requires a disposable PostgreSQL database")
    config.addinivalue_line("markers", "live: contacts a real external service")

    import config as app_config
    db_url = getattr(app_config, "DATABASE_URL", "") or ""
    if not db_url:
        raise pytest.UsageError("DATABASE_URL is empty after config load -- refusing to run tests with no identified database.")

    if _PRODUCTION_DB_HOST_FRAGMENT in db_url:
        raise pytest.UsageError(
            "Tests may never run against the production database. Provision a "
            "disposable database and pass its URL as TEST_DATABASE_URL."
        )


_POSTGRES_TEST_FILES = {
    "tests/uchoice_outbound/test_before_persistence_validation.py",
    "tests/uchoice_outbound/test_outbound_validation_regressions.py",
    "tests/uchoice_outbound_pdf/test_pdf_timing.py",
    "tests/uchoice_storage_atomicity/test_storage_atomicity_regressions.py",
    "tests/uchoice_storage_atomicity/test_engine_split_boundaries.py",
    "tests/uchoice_storage_atomicity/test_pre_confirm_validators.py",
    "tests/uchoice_storage_atomicity/test_reply_failure_does_not_roll_back_inventory.py",
}


def pytest_collection_modifyitems(config, items):
    """Classify database tests and skip them unless explicitly configured."""
    # Only a value supplied to the pytest process is an opt-in. A value loaded
    # later from a developer's .env file must not silently enable DB tests.
    has_test_database = bool(_EXPLICIT_TEST_DATABASE_URL)
    skip = pytest.mark.skip(reason="requires TEST_DATABASE_URL pointing to disposable PostgreSQL")

    for item in items:
        path = str(item.path).replace("\\", "/")
        relative = path.split("/tests/", 1)[-1]
        relative = f"tests/{relative}" if not relative.startswith("tests/") else relative
        is_postgres = "/kefu_integration/" in f"/{path}" or relative in _POSTGRES_TEST_FILES
        if is_postgres:
            item.add_marker(pytest.mark.postgres)
            if not has_test_database:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def block_operational_clients(monkeypatch):
    """
    Block both client exports and aliases imported into production modules.
    """

    def blocked(*_args, **_kwargs):
        raise AssertionError("operational client call attempted in a test")

    # Layer 1: the client modules' own exports.
    monkeypatch.setattr("clients.wechat_client.send_message", blocked)
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_message", blocked)
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_file", blocked)
    monkeypatch.setattr("clients.oms_client.query_outbound_order", blocked)
    monkeypatch.setattr("clients.oms_client.create_work_order", blocked)
    monkeypatch.setattr("clients.yidida_client.create_label", blocked)

    # Layer 2: already-bound aliases in every module that imports these by
    # value (`from clients.x import y`) -- confirmed by direct grep of every
    # such import site in the repository.
    monkeypatch.setattr("core.workflow_engine._send_raw", blocked)
    monkeypatch.setattr("handlers.reply_wechat.send_message", blocked)
    monkeypatch.setattr("handlers.label.base.create_label", blocked)
    monkeypatch.setattr("handlers.oms_create_workorder.query_outbound_order", blocked)
    monkeypatch.setattr("handlers.oms_create_workorder.create_work_order", blocked)
    monkeypatch.setattr("jobs.session_expiry.send_group_webhook_message", blocked)
    monkeypatch.setattr("jobs.uchoice_daily.send_group_webhook_message", blocked)
    monkeypatch.setattr("jobs.uchoice_invoice.send_group_webhook_message", blocked)
    monkeypatch.setattr("api.webhook.send_message", blocked)

    # Layer 3: transport-level kill switch. All current operational clients
    # route through requests.post, which flows through Session.request --
    # catches any call this isolation didn't anticipate (e.g. a future
    # local-import call site, or a new client added later without updating
    # layers 1-2 here).
    import requests.sessions

    def blocked_request(self, method, url, *args, **kwargs):
        raise AssertionError(f"blocked outbound HTTP request in a test: {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)
