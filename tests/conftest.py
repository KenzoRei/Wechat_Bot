"""
Shared safety guards for every test package under tests/ that touches the
real dev database, per the signed plan's layered isolation requirement
(systemic-validation-addendum.md Sec 5 / agreed-plan.md Sec 7) and Codex
round-30's finding 4: patching only the public client module exports is not
enough, because production code imports these functions by value
(`from clients.x import y`) -- the bound alias in the importing module keeps
its own reference to the original function, unaffected by patching the
source module's attribute. Every layer below must be patched separately.

Also does NOT replace tests/uchoice_lifecycle/conftest.py, which still runs
its own (SQLite-scoped) block for its own directory.
"""
import os
import pytest


# The production callback is always mounted. Tests use deterministic callback
# crypto so importing config never depends on developer or deployment secrets.
os.environ.setdefault("WECHAT_KEFU_TOKEN", "test-kefu-callback-token")
os.environ.setdefault(
    "WECHAT_KEFU_ENCODING_AES_KEY",
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
)


_EXPECTED_DB_HOST_FRAGMENT = "dpg-d9p26h7lk1mc73a841d0-a.virginia-postgres.render.com"


def pytest_configure(config):
    # Import config (not raw os.environ) so .env has actually been loaded by
    # the time this check runs -- checking os.environ directly at
    # pytest_configure time could see an empty value that gets populated
    # later by config.py's load_dotenv(), silently skipping the check
    # (Codex round-30 finding 4, first half).
    import config as app_config
    db_url = getattr(app_config, "DATABASE_URL", "") or ""
    if not db_url:
        raise pytest.UsageError("DATABASE_URL is empty after config load -- refusing to run tests with no identified database.")
    if "sqlite" not in db_url and _EXPECTED_DB_HOST_FRAGMENT not in db_url:
        raise pytest.UsageError(
            f"DATABASE_URL does not point at the known dev database "
            f"({_EXPECTED_DB_HOST_FRAGMENT}) -- refusing to run tests against "
            f"an unrecognized database. Got: {db_url!r}"
        )


@pytest.fixture(autouse=True)
def block_operational_clients(monkeypatch):
    """
    Layered blocking, per Codex round-30 finding 4 / the signed isolation
    design: patching the client modules alone is insufficient, since
    production code holds its own bound references.

    Fails closed: if any listed target can't be found/patched, this raises
    at fixture setup rather than silently leaving a gap.
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
