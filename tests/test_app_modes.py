"""
Startup-mode truthfulness tests:

- SMART_ROBOT_ENABLED=false must actually remove the Smart Bot route from
  the app, not just skip its cron jobs.
- KEFU_CALLBACK_ENABLED (separate from KEFU_ENABLED) must actually remove
  the Kefu callback route and stop requiring its credentials.
- KEFU_ENABLED=true with KEFU_CALLBACK_ENABLED=false must fail fast at
  config import, not silently starve Kefu processing of new messages.
- Invalid boolean values for deployment-mode flags must fail fast, not
  silently coerce to false.

Route presence is checked via TestClient HTTP calls, not by introspecting
`app.routes` directly because the installed FastAPI/Starlette routing
representation does not expose a flat `.path` on every entry the way
an earlier version of this file assumed, causing two false failures even
though the routes were actually being gated correctly). A missing route
returns 404; an existing route called without its required query params
returns 422 (FastAPI's own request-validation response) -- proving the
route matched before failing, which is the strongest available signal
short of a full valid request.

Restoration snapshots and restores the exact original environment values
itself. Relying on monkeypatch teardown ordering does
not guarantee across two independently-requested function-scoped fixtures
with no explicit dependency between them).

Requires a fully configured environment (same as every other test here) --
config.py's _require() calls need every setting present regardless of which
flag this test is toggling.
"""
import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


_KEFU_ONLY_CREDENTIAL_KEYS = [
    "WECHAT_KEFU_TOKEN", "WECHAT_KEFU_ENCODING_AES_KEY",
    "WECHAT_KEFU_SECRET", "WECHAT_KEFU_OPEN_KFID", "KEFU_GROUP_ID",
]
_TRACKED_KEYS = [
    "SMART_ROBOT_ENABLED", "KEFU_ENABLED", "KEFU_CALLBACK_ENABLED", "RUN_SCHEDULER",
    *_KEFU_ONLY_CREDENTIAL_KEYS,
]


def _route_exists(app, path: str, method: str = "get") -> bool:
    """
    A 404 means the route isn't registered at all. Any other status
    (422 for missing required query params being the expected case here,
    but even a 403/500 from actually executing the handler) means it
    matched a real route -- the thing this helper needs to distinguish.
    """
    client = TestClient(app, raise_server_exceptions=False)
    resp = getattr(client, method)(path)
    return resp.status_code != 404


@pytest.fixture
def reload_app():
    """
    Yields a function that sets the given env vars (empty string for a key
    that should be explicitly ABSENT -- python-dotenv's default
    override=False means it will only fill in a key that's entirely
    missing from os.environ, not one already present as "", so this
    reliably keeps .env from repopulating it on reload), reloads config +
    main, and returns the reloaded main module.

    Restores the exact original environment (including keys that were
    unset before the test) and reloads config + main back to that state on
    teardown, regardless of what monkeypatch itself has or hasn't reverted
    yet -- this fixture does not use monkeypatch at all, for exactly that
    reason.
    """
    original = {k: os.environ.get(k) for k in _TRACKED_KEYS}

    def _reload(**env: str):
        for key, value in env.items():
            os.environ[key] = value
        import config
        importlib.reload(config)
        import main
        importlib.reload(main)
        return main

    yield _reload

    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    if "main" in sys.modules:
        importlib.reload(sys.modules["main"])


def test_smart_robot_disabled_removes_webhook_route(reload_app):
    main = reload_app(SMART_ROBOT_ENABLED="false")
    assert not _route_exists(main.app, "/webhook", "post"), (
        "SMART_ROBOT_ENABLED=false must remove /webhook from the app, not just skip its cron jobs"
    )
    assert "api.webhook" not in sys.modules or main._smart_robot_router is None


def test_smart_robot_enabled_mounts_webhook_route(reload_app):
    main = reload_app(SMART_ROBOT_ENABLED="true")
    # POST /webhook requires msg_signature/timestamp/nonce query params --
    # calling with none must 422 (route matched, validation failed), not 404.
    resp = TestClient(main.app, raise_server_exceptions=False).post("/webhook")
    assert resp.status_code == 422, f"expected 422 proving the route exists, got {resp.status_code}"


def test_smart_robot_disabled_removes_its_scheduled_jobs(reload_app):
    main = reload_app(SMART_ROBOT_ENABLED="false")
    job_ids = {job.id for job in main.scheduler.get_jobs()}
    assert "uchoice_daily" not in job_ids
    assert "uchoice_invoice" not in job_ids
    assert "session_expiry" in job_ids, "the channel-neutral expiry job must still run"


def test_kefu_callback_disabled_removes_callback_route_and_credential_requirement(reload_app):
    # Explicitly absent (not just "flag off") -- proves these aren't merely
    # tolerated when present, but genuinely unnecessary in this mode.
    # Reload succeeding at all is itself part of what's being tested: no
    # _require() call may fire for any of these five in this mode.
    main = reload_app(
        KEFU_ENABLED="false", KEFU_CALLBACK_ENABLED="false",
        **{k: "" for k in _KEFU_ONLY_CREDENTIAL_KEYS},
    )
    assert not _route_exists(main.app, "/kefu/callback"), (
        "KEFU_CALLBACK_ENABLED=false must remove /kefu/callback, unlike the old always-mounted behavior"
    )
    import config
    for key in _KEFU_ONLY_CREDENTIAL_KEYS:
        assert not getattr(config, key, None), f"{key} should be empty/unset in this mode"


def test_kefu_callback_enabled_by_default_mounts_route(reload_app):
    main = reload_app(KEFU_ENABLED="false", KEFU_CALLBACK_ENABLED="true")
    resp = TestClient(main.app, raise_server_exceptions=False).get("/kefu/callback")
    assert resp.status_code == 422, f"expected 422 proving the route exists, got {resp.status_code}"


def test_kefu_enabled_without_callback_fails_fast():
    """
    KEFU_ENABLED=true has no way to discover new messages without the
    callback -- config.py must refuse to start rather than silently run a
    Kefu deployment that never receives anything.
    """
    original = {k: os.environ.get(k) for k in ("KEFU_ENABLED", "KEFU_CALLBACK_ENABLED")}
    try:
        os.environ["KEFU_ENABLED"] = "true"
        os.environ["KEFU_CALLBACK_ENABLED"] = "false"
        import config
        with pytest.raises(RuntimeError, match="KEFU_CALLBACK_ENABLED"):
            importlib.reload(config)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if "config" in sys.modules:
            importlib.reload(sys.modules["config"])
        if "main" in sys.modules:
            importlib.reload(sys.modules["main"])


def test_invalid_boolean_flag_fails_fast_instead_of_silently_becoming_false():
    """A typo like 'tru' or 'yes' must not silently disable a channel -- it must refuse to start."""
    original = os.environ.get("SMART_ROBOT_ENABLED")
    try:
        os.environ["SMART_ROBOT_ENABLED"] = "yes"
        import config
        with pytest.raises(RuntimeError, match="SMART_ROBOT_ENABLED"):
            importlib.reload(config)
    finally:
        if original is None:
            os.environ.pop("SMART_ROBOT_ENABLED", None)
        else:
            os.environ["SMART_ROBOT_ENABLED"] = original
        if "config" in sys.modules:
            importlib.reload(sys.modules["config"])
        if "main" in sys.modules:
            importlib.reload(sys.modules["main"])
