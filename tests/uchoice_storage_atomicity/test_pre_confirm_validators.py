"""
Codex round-30 finding 2: adjust_storage/move_storage/recount_storage had
zero registered PRE_CONFIRM_VALIDATORS entries. Confirms the new entries
reject a fabricated/missing sku_code and accept a real one, for each of the
three services Claude Code owns in Phase 2.
"""
import pytest

from database import SessionLocal
from core import pre_confirm_validators


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.mark.parametrize("service_name,field_name,extra_fields", [
    ("adjust_storage", "adjustment_lines", {"boxes_per_pallet": 80, "pallet_delta": 3}),
    ("move_storage", "move_lines", {"source_boxes_per_pallet": 80, "box_count_moved": 5, "target_boxes_per_pallet": 64}),
    ("recount_storage", "inventory_lines", {"boxes_per_pallet": 80, "pallet_count": 5}),
])
def test_rejects_fabricated_sku(db, service_name, field_name, extra_fields):
    error = pre_confirm_validators.run(
        service_name, {}, {field_name: [{"sku_code": "fabricated-nonexistent", **extra_fields}]}, db,
    )
    assert error is not None


@pytest.mark.parametrize("service_name,field_name,extra_fields", [
    ("adjust_storage", "adjustment_lines", {"boxes_per_pallet": 80, "pallet_delta": 3}),
    ("move_storage", "move_lines", {"source_boxes_per_pallet": 80, "box_count_moved": 5, "target_boxes_per_pallet": 64}),
    ("recount_storage", "inventory_lines", {"boxes_per_pallet": 80, "pallet_count": 5}),
])
def test_accepts_real_catalog_sku(db, service_name, field_name, extra_fields):
    error = pre_confirm_validators.run(
        service_name, {}, {field_name: [{"sku_code": "s1", **extra_fields}]}, db,
    )
    assert error is None
