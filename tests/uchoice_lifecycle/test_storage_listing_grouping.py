"""
view_storage (and the shared post-mutation "updated storage" summary) used
to cram every bucket for a SKU onto one comma-joined line -- unreadable
once odd leftover buckets accumulate from repackaging/partial picks. Each
multi-bucket SKU now gets its own mini-section instead.
"""
from types import SimpleNamespace

from core.message_sections import render_sections
from core.result_message import _storage_sections_builder, _warehouse_storage_summary_sections


class _Query:
    def __init__(self, catalog):
        self.catalog = catalog

    def all(self):
        return [SimpleNamespace(sku_code=code, description=desc) for code, desc in self.catalog.items()]

    def filter_by(self, **kwargs):
        self._warehouse = kwargs.get("warehouse_code")
        return self


class _StorageQuery:
    def __init__(self, rows):
        self.rows = rows
        self._warehouse = None

    def filter_by(self, **kwargs):
        self._warehouse = kwargs.get("warehouse_code")
        return self

    def filter(self, *args):
        # Only real caller filter is pallet_count > 0 -- apply it directly
        # rather than trying to evaluate the SQLAlchemy expression.
        self.rows = [r for r in self.rows if r["pallet_count"] > 0]
        return self

    def all(self):
        return [SimpleNamespace(**r) for r in self.rows if r["warehouse_code"] == self._warehouse]


class _DB:
    def __init__(self, catalog, storage_rows):
        self.catalog = catalog
        self.storage_rows = storage_rows

    def query(self, model):
        from models.uchoice import UchoiceSku, UchoiceStorage
        if model is UchoiceSku:
            return _Query(self.catalog)
        if model is UchoiceStorage:
            return _StorageQuery(self.storage_rows)
        raise AssertionError(f"unexpected model queried: {model}")


_CATALOG = {"s2": "S2 1500 ft Stretch Wrap", "t1": "T1 3-inch Clear Packing Tape"}


def test_multi_bucket_sku_gets_its_own_section_not_one_crammed_line():
    db = _DB(_CATALOG, [])
    context = {"result": {"storage_rows": [
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 37, "pallet_count": 1},
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 64, "pallet_count": 10},
    ]}}
    sections = _storage_sections_builder(context, db)
    text = "\n".join(render_sections(sections))

    assert "**JFK 仓（共 11 托）**" in text
    assert "**S2 1500 ft Stretch Wrap（合计 11 托）**" in text
    assert "- 1 托 @ 37/托" in text
    assert "- 10 托 @ 64/托" in text
    # The old cramped single-line format must be gone.
    assert "1 托 @ 37/托，10 托 @ 64/托" not in text


def test_zero_count_buckets_are_dropped_from_the_listing():
    """
    A leftover-pallet bucket that's since been fully consumed (e.g. by a
    move/pick) stays a row in uchoice_storage -- nothing deletes it -- so
    it must never show up as a meaningless "0 托 @ X/托" line. Also proves
    a SKU whose ONLY bucket dropped to zero disappears from the listing
    entirely, not left as a 0-total heading.
    """
    db = _DB(_CATALOG, [])
    context = {"result": {"storage_rows": [
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 37, "pallet_count": 1},
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 64, "pallet_count": 10},
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 70, "pallet_count": 0},
        {"warehouse_code": "JFK", "sku_code": "t1", "boxes_per_pallet": 105, "pallet_count": 0},
    ]}}
    sections = _storage_sections_builder(context, db)
    text = "\n".join(render_sections(sections))

    assert "70/托" not in text  # s2's zero bucket must be gone
    assert "T1 3-inch Clear Packing Tape" not in text  # its only bucket was zero -- vanishes entirely
    assert "**S2 1500 ft Stretch Wrap（合计 11 托）**" in text  # total unaffected -- zero contributed 0 anyway


def test_single_bucket_sku_stays_one_line_no_separate_heading():
    db = _DB(_CATALOG, [])
    context = {"result": {"storage_rows": [
        {"warehouse_code": "JFK", "sku_code": "t1", "boxes_per_pallet": 105, "pallet_count": 4},
    ]}}
    sections = _storage_sections_builder(context, db)
    text = "\n".join(render_sections(sections))

    assert "- T1 3-inch Clear Packing Tape：4 托 @ 105/托" in text
    assert "T1 3-inch Clear Packing Tape（合计" not in text  # no redundant heading for one fact


def test_completion_storage_summary_uses_the_same_grouped_format():
    db = _DB(_CATALOG, [
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 37, "pallet_count": 1},
        {"warehouse_code": "JFK", "sku_code": "s2", "boxes_per_pallet": 64, "pallet_count": 10},
    ])
    sections = _warehouse_storage_summary_sections(db, "JFK", "仓当前库存")
    text = "\n".join(render_sections(sections))

    assert "**JFK 仓当前库存（共 11 托）**" in text
    assert "**S2 1500 ft Stretch Wrap（合计 11 托）**" in text
    assert "- 1 托 @ 37/托" in text
    assert "- 10 托 @ 64/托" in text
