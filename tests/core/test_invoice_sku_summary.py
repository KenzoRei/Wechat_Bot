"""
Invoice SKU Lines formatting (core/uchoice_invoice_export._sku_summary):
every line shows its quantity with a unit, so a loose box never reads like
a pallet, and a picks-only line never shows "x?".
"""
from core.uchoice_invoice_export import _line_quantity, _sku_summary

LABELS = {"t1": "T1 3-inch Clear Packing Tape", "s2": "S2 1500 ft Stretch Wrap"}


def test_pallet_and_loose_lines_carry_units():
    lines = [
        {"sku_code": "t1", "boxes_per_pallet": 105, "pallet_count": 1},
        {"sku_code": "s2", "box_count": 1},
    ]
    assert _sku_summary(lines, LABELS) == "T1 3-inch Clear Packing Tape x1托; S2 1500 ft Stretch Wrap 散箱x1"


def test_picks_only_line_shows_picked_boxes():
    lines = [{"sku_code": "t1", "picks": [
        {"source_boxes_per_pallet": 50, "box_count": 50},
        {"source_boxes_per_pallet": 105, "box_count": 55},
    ]}]
    assert _line_quantity(lines[0]) is None
    assert _sku_summary(lines, LABELS) == "T1 3-inch Clear Packing Tape 105箱"


def test_line_without_any_quantity_still_marks_unknown():
    assert _sku_summary([{"sku_code": "zz"}], LABELS) == "zz x?"
