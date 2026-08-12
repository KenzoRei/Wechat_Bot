"""Pure phrase-matching/role-gating tests -- no DB needed for these paths."""
from core.kefu_admin_purge import (
    CONFIRM_COMMAND,
    TRIGGER_COMMAND,
    is_purge_confirm,
    is_purge_trigger,
    try_handle_purge_command,
)


def test_trigger_phrase_matches_exactly():
    assert is_purge_trigger(TRIGGER_COMMAND)


def test_trigger_phrase_tolerates_surrounding_whitespace():
    assert is_purge_trigger(f"  {TRIGGER_COMMAND}  ")


def test_trigger_phrase_rejects_near_miss():
    assert not is_purge_trigger("清空所有会话")
    assert not is_purge_trigger("请清空所有进行中会话")


def test_confirm_phrase_matches_exactly():
    assert is_purge_confirm(CONFIRM_COMMAND)


def test_confirm_phrase_rejects_bare_confirm():
    assert not is_purge_confirm("确认")


def test_non_admin_never_triggers_regardless_of_exact_phrase(monkeypatch):
    # role check is the very first gate -- must short-circuit before any
    # DB access, so passing db=None here proves it never even tries.
    assert try_handle_purge_command(None, "customer", TRIGGER_COMMAND) is None
    assert try_handle_purge_command(None, "warehouseman", CONFIRM_COMMAND) is None


def test_unrelated_message_returns_none_for_admin(monkeypatch):
    assert try_handle_purge_command(None, "admin", "帮我查一下库存") is None
