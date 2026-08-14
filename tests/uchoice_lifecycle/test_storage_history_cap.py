"""
view_storage_history's reply used to list every matching movement with no
cap -- a full month easily exceeded WeCom Kefu's hard 2048-UTF-8-byte
send_text limit, confirmed live as a 100% silent delivery failure for any
content-rich reply. The detail list is now capped to the latest 10
movements; the net-change summary still covers the full queried range.
"""
from types import SimpleNamespace

from core.message_sections import render_sections
from core.result_message import _storage_history_sections_builder


class _Query:
    def all(self):
        return [SimpleNamespace(sku_code="s1", description="S1 Widget")]


class _DB:
    def query(self, model):
        return _Query()


def _row(day: int, delta: int, sku="s1", txn_type="inbound"):
    return {
        "created_at": f"2026-08-{day:02d}T10:00:00+00:00",
        "txn_type": txn_type,
        "sku_code": sku,
        "boxes_per_pallet": 40,
        "pallet_delta": delta,
    }


def test_small_result_shows_every_row_no_truncation_notice():
    rows = [_row(d, 1) for d in range(1, 6)]  # 5 rows
    context = {"result": {"history_rows": rows, "range_start": "2026-08-01", "range_end": "2026-08-05"}}
    sections = _storage_history_sections_builder(context, _DB())
    text = "\n".join(render_sections(sections))

    for day in range(1, 6):
        assert f"- 08-{day:02d} 10:00" in text
    assert "如需查看完整记录" not in text


def test_large_result_caps_detail_to_latest_10_but_keeps_full_net_total():
    rows = [_row(d, 1) for d in range(1, 21)]  # 20 rows, net should be +20
    context = {"result": {"history_rows": rows, "range_start": "2026-08-01", "range_end": "2026-08-20"}}
    sections = _storage_history_sections_builder(context, _DB())
    text = "\n".join(render_sections(sections))

    # Only the latest 10 (days 11-20) appear as detail lines.
    for day in range(11, 21):
        assert f"- 08-{day:02d} 10:00" in text
    for day in range(1, 11):
        assert f"- 08-{day:02d} 10:00" not in text

    assert "最近 10 条（共 20 条）" in text
    assert "如需查看完整记录，请说明需要导出明细表格。" in text
    # Net-by-SKU total reflects ALL 20 rows, not just the displayed 10.
    assert "S1 Widget：+20 托" in text
    assert "合计：+20 托" in text
