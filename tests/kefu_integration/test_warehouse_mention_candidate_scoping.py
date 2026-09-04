"""
Regression coverage for the same-turn warehouse-mention candidate-scoping
fix in core/session_manager.py's _build_uchoice_candidates().

Live incident: an unscoped caller (admin role, no warehouse restriction)
sent "送一版T5，从NJ仓到DE仓" as their very first message -- naming both a
source and a destination warehouse in one breath. _build_uchoice_candidates
runs BEFORE the AI call that would otherwise extract warehouse_code from
that same message, so with collected_fields still empty, the address
candidate list defaulted to JFK-only (core/uchoice_context.py's per-
warehouse address book means each warehouse has its OWN "DE Warehouse" row
for its own outbound transfers to DE) -- the real NJ-scoped "DE Warehouse"
address was never even offered to the AI as a candidate. It could only
match the JFK-scoped one, which core/pre_confirm_validators.py then
correctly rejected at confirmation time: a needless failure, not a safety
gap (the validator is the real backstop either way).

Fix: _mentioned_warehouse_codes() scans the raw incoming message text for
bare JFK/DE/NJ mentions as a same-turn scoping hint, used only when neither
collected_fields nor the caller's own warehouse assignment has already
resolved one.

Real Postgres DB; read-only against the shared V24 NJ/DE/JFK address-book
fixture rows and the shared fixture group -- this module never writes.
"""
from database import SessionLocal
from models.group import GroupConfig
from models.role import Role
from core import access_control, session_manager

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _admin_access(db):
    """
    An unscoped caller (warehouse_codes=None) is the exact shape that hit
    the live incident -- a caller restricted to specific warehouses
    (e.g. warehouseman) never reaches the buggy default branch at all,
    since their own assignment already scopes the candidate list.
    """
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    role = db.query(Role).filter_by(name="admin").one()
    from models.group import GroupMember
    member = db.query(GroupMember).filter_by(group_id=group.group_id, role_id=role.role_id).first()
    assert member is not None, "no admin group member in the fixture group -- seed data missing"
    assert member.warehouse_codes is None, "test assumes an unscoped admin caller"
    return access_control.check_access(db, member.wechat_openid, WECHAT_GROUP_ID)


def test_dual_mention_widens_address_candidates_to_both_named_warehouses():
    """The exact live incident: 'from NJ to DE' in one message must surface
    the NJ-scoped 'DE Warehouse' address, not just the JFK-scoped default."""
    db = SessionLocal()
    try:
        access = _admin_access(db)
        candidates = session_manager._build_uchoice_candidates(
            db, access, session=None, message_content="送一版T5，从NJ仓到DE仓"
        )
        by_id = {c["address_id"]: c for c in candidates["addresses"]}
        assert "a1000000-0024-0000-0000-000000000003" in by_id, (
            "the NJ-scoped 'DE Warehouse' address must be offered as a candidate "
            "when the message itself names NJ as the source warehouse"
        )
        assert by_id["a1000000-0024-0000-0000-000000000003"]["warehouse_code"] == "NJ"
    finally:
        db.close()


def test_single_mention_scopes_to_that_warehouse_only():
    db = SessionLocal()
    try:
        access = _admin_access(db)
        candidates = session_manager._build_uchoice_candidates(
            db, access, session=None, message_content="从NJ仓出库"
        )
        by_id = {c["address_id"]: c for c in candidates["addresses"]}
        assert "a1000000-0024-0000-0000-000000000003" in by_id, \
            "NJ-scoped 'DE Warehouse' must be a candidate when NJ is the only mentioned warehouse"
        # The JFK-scoped "DE Warehouse" (a different row, same label) must
        # NOT leak in -- a single unambiguous mention should scope exactly
        # like an already-known warehouse_code would.
        assert "8d2f7c73-122a-4d90-8424-03e1081b2b34" not in by_id
    finally:
        db.close()


def test_no_mention_keeps_existing_jfk_default_unchanged():
    """Regression guard: a message with no warehouse mention at all must
    fall back to the pre-existing JFK default, exactly as before this fix."""
    db = SessionLocal()
    try:
        access = _admin_access(db)
        candidates = session_manager._build_uchoice_candidates(
            db, access, session=None, message_content="帮我查一下库存"
        )
        by_id = {c["address_id"]: c for c in candidates["addresses"]}
        assert "8d2f7c73-122a-4d90-8424-03e1081b2b34" in by_id, "JFK-scoped default must be unchanged"
        assert "a1000000-0024-0000-0000-000000000003" not in by_id
    finally:
        db.close()


def test_mention_in_english_word_is_not_false_positive():
    """'NJDevOps' must not be mistaken for a bare 'NJ' mention -- the
    default JFK scoping should apply exactly as if nothing were mentioned."""
    db = SessionLocal()
    try:
        access = _admin_access(db)
        candidates = session_manager._build_uchoice_candidates(
            db, access, session=None, message_content="请问NJDevOps团队的库存"
        )
        by_id = {c["address_id"]: c for c in candidates["addresses"]}
        assert "8d2f7c73-122a-4d90-8424-03e1081b2b34" in by_id
        assert "a1000000-0024-0000-0000-000000000003" not in by_id
    finally:
        db.close()
