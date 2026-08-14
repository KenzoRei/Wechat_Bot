"""
Kefu-side registration intake, mirroring test_registration_command.py's
coverage
against core.kefu_registration instead of core.self_registration.
"""
import pytest
from sqlalchemy.exc import OperationalError

import config
from core import kefu_registration
from core.kefu_contracts import KefuIdentity
from tests.uchoice_self_registration._kefu_mock_db import KefuMockDB

IDENTITY = KefuIdentity(open_kfid="kf-1", external_userid="staff-1")


@pytest.fixture(autouse=True)
def _kefu_group_id(monkeypatch):
    """
    config.KEFU_GROUP_ID's module-level value depends on import order across
    the whole test session (whichever test module first triggers `import
    config` freezes it from whatever env was set at that moment) -- setting
    os.environ here would be too fragile. Patch the attribute directly
    instead, regardless of when/how config was first imported.
    """
    monkeypatch.setattr(config, "KEFU_GROUP_ID", "kefu-group-uuid-1")


# ── normalization (reuses the identical rule as core.self_registration) ────

@pytest.mark.parametrize("content", [
    "注册成员",
    "  注册成员  ",
    "　注册成员　",
])
def test_exact_command_matches_after_normalization(content):
    assert kefu_registration.is_registration_command(content) is True


@pytest.mark.parametrize("content", [
    "注册成员吧",
    "请帮我注册成员",
    "注册",
    "成员",
    "",
    "hello",
])
def test_non_exact_variants_do_not_match(content):
    assert kefu_registration.is_registration_command(content) is False


# ── gating preconditions ────────────────────────────────────────────────────

def test_arbitrary_text_does_not_register():
    db = KefuMockDB()
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "随便说点什么")
    assert reply is None
    assert db.commit_count == 0


# ── successful registration ─────────────────────────────────────────────────

def test_brand_new_identity_registers_into_pending():
    db = KefuMockDB(existing_member_role=None)
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._REGISTERED_REPLY
    assert db.commit_count == 1
    assert len(db.added) == 1
    assert db.added[0].role_id == "role-pending"
    assert db.added[0].open_kfid == "kf-1"
    assert db.added[0].external_userid == "staff-1"
    assert db.added[0].group_id == "kefu-group-uuid-1"


def test_registration_always_uses_fixed_configured_group_id_never_from_message():
    """group_id always comes from the fixed deployment mapping, never staff
    input or message inference."""
    db = KefuMockDB(existing_member_role=None)
    kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert db.added[0].group_id == "kefu-group-uuid-1"


def test_missing_pending_role_fails_controlled_not_silent_success():
    db = KefuMockDB(existing_member_role=None, pending_role_exists=False)
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._FAILED_REPLY
    assert db.commit_count == 0
    assert db.rollback_count == 1


# ── retry / duplicate / error semantics ─────────────────────────────────────

def test_pending_retry_gets_awaiting_assignment_reply():
    db = KefuMockDB(existing_member_role="pending")
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._ALREADY_PENDING_REPLY
    assert db.commit_count == 0


def test_operational_identity_retry_gets_distinct_reply_no_role_change():
    db = KefuMockDB(existing_member_role="admin")
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._ALREADY_OPERATIONAL_REPLY
    assert reply != kefu_registration._ALREADY_PENDING_REPLY
    assert db.commit_count == 0


def test_composite_unique_race_maps_to_duplicate_response():
    db = KefuMockDB(existing_member_role=None, integrity_error_constraint="kefu_staff_open_kfid_external_userid_key")
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._ALREADY_PENDING_REPLY
    assert db.rollback_count == 1


def test_unrelated_integrity_error_is_not_mislabeled_as_duplicate():
    db = KefuMockDB(existing_member_role=None, integrity_error_constraint="kefu_staff_group_id_fkey")
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")
    assert reply == kefu_registration._FAILED_REPLY
    assert reply != kefu_registration._ALREADY_PENDING_REPLY
    assert db.rollback_count == 1


def test_non_integrity_database_error_fails_controlled_and_rolls_back():
    class _DatabaseFailureDB(KefuMockDB):
        def commit(self):
            self.commit_count += 1
            raise OperationalError("COMMIT", {}, RuntimeError("simulated database outage"))

    db = _DatabaseFailureDB(existing_member_role=None)
    reply = kefu_registration.try_handle_kefu_registration_command(db, IDENTITY, "注册成员")

    assert reply == kefu_registration._FAILED_REPLY
    assert db.rollback_count == 1


# ── pending short circuit ────────────────────────────────────────────────────

def test_pending_short_circuit_fires_for_pending_role():
    assert kefu_registration.pending_short_circuit_reply("pending") == kefu_registration.PENDING_SHORT_CIRCUIT_REPLY


@pytest.mark.parametrize("role", ["admin", "customer", "warehouseman", "accountant"])
def test_pending_short_circuit_does_not_fire_for_operational_roles(role):
    assert kefu_registration.pending_short_circuit_reply(role) is None
