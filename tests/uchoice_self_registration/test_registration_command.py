"""
phase4-self-registration.md Sec 3 + Sec 8 -- exact-command recognition,
normalization, and precise duplicate/error semantics.
"""
import pytest
from sqlalchemy.exc import OperationalError

from core import self_registration
from tests.uchoice_self_registration._mock_db import MockDB


# ── normalization ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "注册成员",
    "  注册成员  ",       # outer whitespace
    "　注册成员　",  # full-width space
])
def test_exact_command_matches_after_normalization(content):
    assert self_registration.is_registration_command(content) is True


@pytest.mark.parametrize("content", [
    "注册成员吧",             # trailing prose
    "请帮我注册成员",          # leading prose
    "注册",                  # substring
    "成员",                  # substring
    "",
    "hello",
])
def test_non_exact_variants_do_not_match(content):
    assert self_registration.is_registration_command(content) is False


# ── try_handle_registration_command: gating preconditions ──────────────────

def _message(**overrides):
    base = {
        "chat_type": "group",
        "group_id": "wxgroup-1",
        "from_user": "user-1",
        "content": "注册成员",
    }
    base.update(overrides)
    return base


def test_single_chat_does_not_register():
    db = MockDB()
    reply = self_registration.try_handle_registration_command(db, _message(chat_type="single"))
    assert reply is None
    assert db.commit_count == 0


def test_empty_sender_does_not_register():
    db = MockDB()
    reply = self_registration.try_handle_registration_command(db, _message(from_user=""))
    assert reply is None
    assert db.commit_count == 0


def test_missing_group_id_does_not_register():
    db = MockDB()
    reply = self_registration.try_handle_registration_command(db, _message(group_id=""))
    assert reply is None
    assert db.commit_count == 0


def test_arbitrary_text_does_not_register():
    db = MockDB()
    reply = self_registration.try_handle_registration_command(db, _message(content="随便说点什么"))
    assert reply is None
    assert db.commit_count == 0


def test_unknown_inactive_group_creates_nothing():
    db = MockDB(group_active=False)
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply is None
    assert db.commit_count == 0


# ── successful registration ─────────────────────────────────────────────────

def test_brand_new_sender_registers_into_pending():
    db = MockDB(existing_member_role=None)
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._REGISTERED_REPLY
    assert db.commit_count == 1
    assert len(db.added) == 1
    assert db.added[0].role_id == "role-pending"
    assert db.added[0].wechat_openid == "user-1"


def test_same_sender_registers_independently_in_two_groups():
    """Codex round-39 minor wording note: the precondition is the sender's
    absence from group_member in each group, not prior cross-group state."""
    db_a = MockDB(existing_member_role=None)
    db_a.wechat_group_id = "wxgroup-A"
    db_b = MockDB(existing_member_role=None)
    db_b.wechat_group_id = "wxgroup-B"

    reply_a = self_registration.try_handle_registration_command(db_a, _message(group_id="wxgroup-A"))
    reply_b = self_registration.try_handle_registration_command(db_b, _message(group_id="wxgroup-B"))

    assert reply_a == self_registration._REGISTERED_REPLY
    assert reply_b == self_registration._REGISTERED_REPLY
    assert db_a.commit_count == 1
    assert db_b.commit_count == 1


def test_missing_pending_role_fails_controlled_not_silent_success():
    db = MockDB(existing_member_role=None, pending_role_exists=False)
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._FAILED_REPLY
    assert db.commit_count == 0
    assert db.rollback_count == 1


# ── retry / duplicate / error semantics (Codex round-37/39) ────────────────

def test_pending_retry_gets_awaiting_assignment_reply():
    db = MockDB(existing_member_role="pending")
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._ALREADY_PENDING_REPLY
    assert db.commit_count == 0


def test_operational_member_retry_gets_distinct_reply_no_role_change():
    db = MockDB(existing_member_role="admin")
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._ALREADY_OPERATIONAL_REPLY
    assert reply != self_registration._ALREADY_PENDING_REPLY
    assert db.commit_count == 0


def test_composite_pk_race_maps_to_duplicate_response():
    db = MockDB(existing_member_role=None, integrity_error_constraint="group_member_pkey")
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._ALREADY_PENDING_REPLY
    assert db.rollback_count == 1


def test_unrelated_integrity_error_is_not_mislabeled_as_duplicate():
    db = MockDB(existing_member_role=None, integrity_error_constraint="group_member_group_id_fkey")
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._FAILED_REPLY
    assert reply != self_registration._ALREADY_PENDING_REPLY
    assert db.rollback_count == 1


def test_non_integrity_database_error_fails_controlled_and_rolls_back():
    class _DatabaseFailureDB(MockDB):
        def commit(self):
            self.commit_count += 1
            raise OperationalError("COMMIT", {}, RuntimeError("simulated database outage"))

    db = _DatabaseFailureDB(existing_member_role=None)
    reply = self_registration.try_handle_registration_command(db, _message())

    assert reply == self_registration._FAILED_REPLY
    assert db.rollback_count == 1


# ── no session/request_log/AI involvement ───────────────────────────────────

def test_registration_never_touches_session_manager_or_ai(monkeypatch):
    def _boom(*_a, **_kw):
        raise AssertionError("registration must not build a session/context or call the AI")

    monkeypatch.setattr("core.session_manager.resolve_session", _boom, raising=False)
    monkeypatch.setattr("core.session_manager.build_context", _boom, raising=False)

    db = MockDB(existing_member_role=None)
    reply = self_registration.try_handle_registration_command(db, _message())
    assert reply == self_registration._REGISTERED_REPLY


# ── pending short circuit (post-access, non-command messages only) ─────────

def test_pending_short_circuit_fires_for_pending_role():
    assert self_registration.pending_short_circuit_reply("pending") == self_registration.PENDING_SHORT_CIRCUIT_REPLY


@pytest.mark.parametrize("role", ["admin", "customer", "warehouseman", "accountant"])
def test_pending_short_circuit_does_not_fire_for_operational_roles(role):
    assert self_registration.pending_short_circuit_reply(role) is None
