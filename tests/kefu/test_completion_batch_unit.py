"""
Pure-logic tests for core/completion_batch.py -- the number parser, the
selection resolver, the batch exclusion rules, and the allocation
simulation. No database. Plan: docs/reviews/active/2026-09-batch-completion-
confirmation/plan.md (tests 1, 2, and the simulation halves of 11/12/16).
"""
from types import SimpleNamespace

import pytest

from core import completion_batch as cb


# ── parse_partial_reply ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["13", "1 3", "①③", "1、3", "1,3", "1，3", "部分确认 1 3", "1和3", "1和3都确认", "第1和第3个"])
def test_number_replies_parse_to_indices(text):
    assert cb.parse_partial_reply(text, 3) == [1, 3]


def test_all_numbers_is_every_index():
    assert cb.parse_partial_reply("123", 3) == [1, 2, 3]


@pytest.mark.parametrize("text", ["15", "10", "1 1", "0", "部分确认"])
def test_unreadable_number_replies_are_ambiguous(text):
    assert cb.parse_partial_reply(text, 4) == cb.AMBIGUOUS


@pytest.mark.parametrize("text", ["确认", "取消", "全部", "①③，③少发了2箱", "3托", "去掉2", ""])
def test_non_number_replies_go_to_the_ai(text):
    assert cb.parse_partial_reply(text, 3) is None


# ── resolve_selection ────────────────────────────────────────────────────────

SNAPSHOT = ["REQ-1-000001", "REQ-1-000002", "REQ-1-000003"]
ELIGIBLE = SNAPSHOT + ["REQ-1-000004"]


def test_select_all_means_the_snapshot_not_every_eligible():
    serials, notes = cb.resolve_selection({"select_all": True}, SNAPSHOT, ELIGIBLE)
    assert serials == SNAPSHOT
    assert notes == []


def test_indices_resolve_against_snapshot_and_keep_eligible_order():
    serials, _ = cb.resolve_selection({"indices": [3, 1]}, SNAPSHOT, ELIGIBLE)
    assert serials == ["REQ-1-000001", "REQ-1-000003"]


def test_all_except():
    serials, _ = cb.resolve_selection({"select_all": True, "exclude_indices": [2]}, SNAPSHOT, ELIGIBLE)
    assert serials == ["REQ-1-000001", "REQ-1-000003"]


def test_unique_serial_suffix_matches_and_unlisted_serial_is_selectable():
    serials, _ = cb.resolve_selection({"serials": ["000004", "REQ-1-000002"]}, SNAPSHOT, ELIGIBLE)
    assert serials == ["REQ-1-000002", "REQ-1-000004"]


def test_invented_serial_and_bad_index_are_dropped_with_notes():
    serials, notes = cb.resolve_selection({"serials": ["REQ-9-999999"], "indices": [7]}, SNAPSHOT, ELIGIBLE)
    assert serials == []
    assert any("REQ-9-999999" in n for n in notes)
    assert any("7" in n for n in notes)


def test_no_longer_eligible_snapshot_item_is_dropped():
    serials, notes = cb.resolve_selection({"select_all": True}, SNAPSHOT, ["REQ-1-000001", "REQ-1-000003"])
    assert serials == ["REQ-1-000001", "REQ-1-000003"]
    assert any("REQ-1-000002" in n for n in notes)


def test_cap_keeps_the_oldest_nine():
    eligible = [f"REQ-1-{i:06d}" for i in range(1, 13)]
    serials, notes = cb.resolve_selection({"serials": eligible}, eligible[:9], eligible)
    assert serials == eligible[:9]
    assert notes


def test_garbage_selection_resolves_to_nothing():
    assert cb.resolve_selection("全部", SNAPSHOT, ELIGIBLE) == ([], [])
    assert cb.resolve_selection({"indices": "1,3"}, SNAPSHOT, ELIGIBLE) == ([], [])


def test_snapshot_is_capped_and_ordered():
    candidates = [{"serial_number": f"REQ-1-{i:06d}"} for i in range(1, 13)]
    assert cb.snapshot_serials(candidates) == [f"REQ-1-{i:06d}" for i in range(1, 10)]


# ── exclusions ───────────────────────────────────────────────────────────────

def _target(serial, sku_lines, warehouse="WA", destination=None, direction="outbound", status="processing"):
    return cb.BatchTarget(
        serial=serial,
        log=SimpleNamespace(status=status),
        direction=direction,
        original_fields={"warehouse_code": warehouse, "sku_lines": sku_lines},
        warehouse_code=warehouse,
        destination_warehouse_code=destination,
    )


def test_loose_inbound_is_excluded():
    t = _target("R1", [{"sku_code": "p", "box_count": 20}], direction="inbound")
    assert "散箱" in cb.ineligibility_reason(t, "inbound")


def test_loose_internal_transfer_is_excluded_but_loose_external_outbound_is_not():
    loose = [{"sku_code": "p", "box_count": 20}]
    assert "散箱" in cb.ineligibility_reason(_target("R1", loose, destination="WB"), "outbound")
    assert cb.ineligibility_reason(_target("R2", loose), "outbound") is None


def test_whole_pallet_transfer_is_allowed():
    t = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], destination="WB")
    assert cb.ineligibility_reason(t, "outbound") is None


def test_wrong_direction_and_status_are_excluded():
    t = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], direction="inbound")
    assert cb.ineligibility_reason(t, "outbound")
    t = _target("R2", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], status="cancelled")
    assert cb.ineligibility_reason(t, "outbound")


# ── simulation ───────────────────────────────────────────────────────────────

def test_partial_pick_leaves_a_smaller_bucket_the_next_target_uses():
    buckets = {("WA", "p"): {100: 1}}
    first = _target("R1", [{"sku_code": "p", "box_count": 40}])
    second = _target("R2", [{"sku_code": "p", "box_count": 30}])
    sim = cb.simulate_allocation(buckets, [first, second])
    assert sim.picks["R1"] == {"p": [{"source_boxes_per_pallet": 100, "box_count": 40}]}
    assert sim.picks["R2"] == {"p": [{"source_boxes_per_pallet": 60, "box_count": 30}]}
    assert sim.final_buckets[("WA", "p")] == {30: 1}
    assert buckets == {("WA", "p"): {100: 1}}  # input untouched


def test_same_request_alone_picks_differently():
    """Why picks can't be persisted as execution input (review finding 2)."""
    buckets = {("WA", "p"): {100: 1}}
    second = _target("R2", [{"sku_code": "p", "box_count": 30}])
    sim = cb.simulate_allocation(buckets, [second])
    assert sim.picks["R2"] == {"p": [{"source_boxes_per_pallet": 100, "box_count": 30}]}


def test_whole_pallet_line_draws_from_smaller_buckets_first():
    buckets = {("WA", "p"): {36: 2, 72: 1}}
    t = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}])
    sim = cb.simulate_allocation(buckets, [t])
    assert sim.picks["R1"] == {"p": [{"source_boxes_per_pallet": 36, "box_count": 72}]}
    assert sim.final_buckets[("WA", "p")] == {72: 1}


def test_shortage_applies_nothing_for_that_target():
    buckets = {("WA", "p"): {72: 1}}
    first = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}])
    second = _target("R2", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}])
    sim = cb.simulate_allocation(buckets, [first, second])
    assert "R1" in sim.picks
    assert sim.shortages["R2"] == [{"sku_code": "p", "requested_boxes": 72, "available_boxes": 0}]


def test_transfer_credits_destination_with_original_packing_not_source_picks():
    """Round-2 finding 1: 2x36 out, 1x72 in."""
    buckets = {("WA", "p"): {36: 2}, ("WB", "p"): {}}
    transfer = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], destination="WB")
    downstream = _target("R2", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], warehouse="WB")
    sim = cb.simulate_allocation(buckets, [transfer, downstream])
    assert sim.picks["R1"] == {"p": [{"source_boxes_per_pallet": 36, "box_count": 72}]}
    assert sim.picks["R2"] == {"p": [{"source_boxes_per_pallet": 72, "box_count": 72}]}
    assert sim.final_buckets[("WA", "p")] == {}
    assert sim.final_buckets[("WB", "p")] == {}


def test_storage_scopes_include_transfer_destinations():
    transfer = _target("R1", [{"sku_code": "p", "boxes_per_pallet": 72, "pallet_count": 1}], destination="WB")
    other = _target("R2", [{"sku_code": "q", "box_count": 5}])
    assert cb.storage_scopes([transfer, other]) == [("WA", "p"), ("WA", "q"), ("WB", "p")]


@pytest.mark.parametrize("text", ["确认", "确认。", " 好的！", "OK", "全部确认", "没问题"])
def test_plain_affirmatives_are_recognized(text):
    assert cb.is_affirmative(text)


@pytest.mark.parametrize("text", [
    "确认，但第三笔少发两箱", "确认第三笔少发两箱", "①③，③少发了2箱", "行吧就这样", "确认前面几个", "不确认", "",
])
def test_anything_else_is_not_affirmative(text):
    """Audit round 4, P1: only an exact affirmative may run a whole batch."""
    assert not cb.is_affirmative(text)


# ── customer-group push (audit round 3, P2) ──────────────────────────────────

class _FakeDB:
    def __init__(self, webhook_url):
        self.group = SimpleNamespace(group_robot_webhook_url=webhook_url)

    def query(self, _model):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return self.group


def _entry(source_channel):
    return {"serial_number": "REQ-1-000001", "group_id": "g", "source_channel": source_channel}


def test_smart_robot_request_gets_deferred_group_push():
    from handlers.uchoice.complete_batch import RunCompletionBatchHandler
    context = {}
    RunCompletionBatchHandler._defer_customer_group_notice(_FakeDB("https://hook"), context, _entry("smart_robot"), "outbound")
    assert context["_deferred_webhook_notifications"] == [{
        "webhook_url": "https://hook",
        "content": "✅ 您的出库申请已完成\n申请编号：REQ-1-000001\n如有问题请联系管理员。",
    }]


def test_kefu_originated_request_gets_no_group_push():
    """Kefu submitters are notified by the pull notice instead."""
    from handlers.uchoice.complete_batch import RunCompletionBatchHandler
    context = {}
    RunCompletionBatchHandler._defer_customer_group_notice(_FakeDB("https://hook"), context, _entry("kefu"), "outbound")
    assert "_deferred_webhook_notifications" not in context


def test_picks_match_ignores_order():
    a = {"p": [{"source_boxes_per_pallet": 36, "box_count": 36}, {"source_boxes_per_pallet": 72, "box_count": 36}]}
    b = {"p": [{"source_boxes_per_pallet": 72, "box_count": 36}, {"source_boxes_per_pallet": 36, "box_count": 36}]}
    assert cb.picks_match(a, b)
    assert not cb.picks_match(a, {"p": [{"source_boxes_per_pallet": 72, "box_count": 72}]})
