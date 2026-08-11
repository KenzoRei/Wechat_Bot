"""
Codex round-32 finding 1: the adjust/move/recount validators previously
only checked SKU membership, not the signed typed contracts (valid bucket
dimensions, non-zero/non-negative counts, unique bucket keys for recount,
box-count-vs-source-dimension ordering for move).

Uses a lightweight mock DB (no real Postgres connection at all -- mirrors
tests/uchoice_lifecycle/test_sku_validation_contracts.py's own pattern)
rather than the real dev database, per the current DB-test-policy
restriction pending the user's explicit decision.
"""
from types import SimpleNamespace

import pytest

from core import pre_confirm_validators
from models.uchoice import UchoiceSku


class _Query:
    def __init__(self, model, catalog):
        self.model = model
        self.catalog = catalog

    def all(self):
        if self.model is UchoiceSku:
            return [SimpleNamespace(sku_code=code, description=desc) for code, desc in self.catalog.items()]
        return []

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return None


class _CatalogDB:
    def __init__(self):
        self.catalog = {"s1": "Product One", "s2": "Product Two"}

    def query(self, model):
        return _Query(model, self.catalog)


@pytest.fixture
def db():
    return _CatalogDB()


# ── adjust_storage ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_line", [
    {"sku_code": "s1", "boxes_per_pallet": 0, "pallet_delta": 1},     # bpp not positive
    {"sku_code": "s1", "boxes_per_pallet": -5, "pallet_delta": 1},    # bpp negative
    {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_delta": 0},    # delta zero
    {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_delta": 1.5},  # delta not int
    {"sku_code": "s1", "boxes_per_pallet": "80", "pallet_delta": 1},  # bpp not int
])
def test_adjust_storage_rejects_invalid_shape(db, bad_line):
    error = pre_confirm_validators.run("adjust_storage", {}, {"adjustment_lines": [bad_line]}, db)
    assert error is not None


def test_adjust_storage_accepts_valid_line(db):
    error = pre_confirm_validators.run(
        "adjust_storage", {},
        {"adjustment_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_delta": -3}]},
        db,
    )
    assert error is None


# ── recount_storage ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_line", [
    {"sku_code": "s1", "boxes_per_pallet": 0, "pallet_count": 5},   # bpp not positive
    {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": -1}, # count negative
    {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 1.5},# count not int
])
def test_recount_storage_rejects_invalid_shape(db, bad_line):
    error = pre_confirm_validators.run("recount_storage", {}, {"inventory_lines": [bad_line]}, db)
    assert error is not None


def test_recount_storage_accepts_zero_count(db):
    """Zero is a legitimate reported count for recount -- must not be rejected."""
    error = pre_confirm_validators.run(
        "recount_storage", {},
        {"inventory_lines": [{"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 0}]},
        db,
    )
    assert error is None


def test_recount_storage_rejects_duplicate_bucket_keys(db):
    """Codex round-32: RecountStorageHandler's dict comprehension silently
    overwrites duplicate (sku_code, boxes_per_pallet) keys -- must be
    rejected before it ever reaches that handler."""
    error = pre_confirm_validators.run(
        "recount_storage", {},
        {"inventory_lines": [
            {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 5},
            {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 9},  # same bucket, different count
        ]},
        db,
    )
    assert error is not None


def test_recount_storage_accepts_distinct_buckets(db):
    error = pre_confirm_validators.run(
        "recount_storage", {},
        {"inventory_lines": [
            {"sku_code": "s1", "boxes_per_pallet": 80, "pallet_count": 5},
            {"sku_code": "s1", "boxes_per_pallet": 64, "pallet_count": 3},  # different bpp, same sku -- fine
            {"sku_code": "s2", "boxes_per_pallet": 80, "pallet_count": 2},
        ]},
        db,
    )
    assert error is None


# ── move_storage ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_line", [
    {"sku_code": "s1", "source_boxes_per_pallet": 0, "box_count_moved": 5, "target_boxes_per_pallet": 64},
    {"sku_code": "s1", "source_boxes_per_pallet": 80, "box_count_moved": 0, "target_boxes_per_pallet": 64},
    {"sku_code": "s1", "source_boxes_per_pallet": 80, "box_count_moved": 5, "target_boxes_per_pallet": 0},
    {"sku_code": "s1", "source_boxes_per_pallet": 80, "box_count_moved": 80, "target_boxes_per_pallet": 64},   # == source, invalid
    {"sku_code": "s1", "source_boxes_per_pallet": 80, "box_count_moved": 90, "target_boxes_per_pallet": 64},   # > source, invalid
])
def test_move_storage_rejects_invalid_shape(db, bad_line):
    error = pre_confirm_validators.run("move_storage", {}, {"move_lines": [bad_line]}, db)
    assert error is not None


def test_move_storage_accepts_valid_line(db):
    error = pre_confirm_validators.run(
        "move_storage", {},
        {"move_lines": [{"sku_code": "s1", "source_boxes_per_pallet": 80, "box_count_moved": 10, "target_boxes_per_pallet": 64}]},
        db,
    )
    assert error is None
