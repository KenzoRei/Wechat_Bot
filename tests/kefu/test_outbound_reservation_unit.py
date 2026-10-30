"""
Pure-logic tests for core/outbound_reservation.py and its call sites -- the
derived reservation of confirmed, uncompleted outbound requests. No database:
scheduled_targets / load_buckets / the storage lock are stubbed. Plan:
docs/ai-collaboration/2026-10-scheduled-outbound-reservation/plan.md.
"""
from types import SimpleNamespace

import pytest

from core import outbound_reservation as res
from core.completion_batch import BatchTarget


def _target(serial, lines, warehouse="JFK", destination_warehouse=None):
    return BatchTarget(
        serial=serial, log=None, direction="outbound",
        original_fields={"warehouse_code": warehouse, "sku_lines": lines},
        warehouse_code=warehouse, destination_warehouse_code=destination_warehouse,
    )


def _stub_world(monkeypatch, physical_buckets, targets=()):
    """physical_buckets: {sku: {boxes_per_pallet: pallets}} for JFK."""
    monkeypatch.setattr(res, "scheduled_targets", lambda db, warehouse_code=None, exclude_log_id=None: [
        t for t in targets if not warehouse_code or t.warehouse_code == warehouse_code
    ])
    monkeypatch.setattr(
        "core.uchoice_storage.available_box_count",
        lambda db, wh, sku, lock=False: sum(b * c for b, c in physical_buckets.get(sku, {}).items()),
    )
    monkeypatch.setattr(
        "core.completion_batch.load_buckets",
        lambda db, scopes: {scope: dict(physical_buckets.get(scope[1], {})) for scope in scopes},
    )


# ── requested_boxes_by_sku ───────────────────────────────────────────────────

def test_loose_and_palletized_lines_are_summed_per_sku():
    fields = {"sku_lines": [
        {"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 2},
        {"sku_code": "s2", "box_count": 6},
        {"sku_code": "t1", "box_count": 5},
    ]}
    assert res.requested_boxes_by_sku(fields) == {"s2": 150, "t1": 5}


def test_incomplete_lines_are_skipped():
    fields = {"sku_lines": [
        {"sku_code": "s2", "boxes_per_pallet": 72},
        {"sku_code": "s2", "pallet_count": 2},
        {"box_count": 5},
        {"sku_code": "t1", "box_count": 0},
    ]}
    assert res.requested_boxes_by_sku(fields) == {}


# ── stock_shortages: scheduled stock is not available to schedule ────────────

def test_request_within_available_is_accepted_and_beyond_is_rejected(monkeypatch):
    _stub_world(monkeypatch, {"t1": {10: 10}}, [_target("A", [{"sku_code": "t1", "box_count": 60}])])

    assert res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 40}]}) == []

    [short] = res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 41}]})
    assert (short.requested_boxes, short.physical_boxes, short.scheduled_boxes, short.available_boxes) == (41, 100, 60, 40)


def test_nothing_scheduled_behaves_like_physical_stock(monkeypatch):
    _stub_world(monkeypatch, {"t1": {10: 10}})
    assert res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 100}]}) == []
    [short] = res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 101}]})
    assert short.scheduled_boxes == 0 and short.available_boxes == 100


def test_loose_reservation_blocks_a_later_palletized_request_on_the_same_sku(monkeypatch):
    # 100 boxes in 10-box pallets; 70 loose boxes already scheduled. A bucket
    # check alone would call 3 free pallets "fine" -- but only 30 boxes are
    # left, so 4 pallets (40) must be rejected and 3 (30) accepted.
    _stub_world(monkeypatch, {"t1": {10: 10}}, [_target("A", [{"sku_code": "t1", "box_count": 70}])])
    four = {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "boxes_per_pallet": 10, "pallet_count": 4}]}
    three = {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "boxes_per_pallet": 10, "pallet_count": 3}]}
    assert [s.sku_code for s in res.stock_shortages(None, four)] == ["t1"]
    assert res.stock_shortages(None, three) == []


def test_palletized_reservation_blocks_a_later_loose_request_on_the_same_sku(monkeypatch):
    _stub_world(monkeypatch, {"t1": {10: 10}}, [_target("A", [{"sku_code": "t1", "boxes_per_pallet": 10, "pallet_count": 7}])])
    assert [s.sku_code for s in res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 31}]})] == ["t1"]
    assert res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 30}]}) == []


def test_reservation_is_per_sku_and_per_warehouse(monkeypatch):
    _stub_world(
        monkeypatch, {"t1": {10: 10}, "s2": {10: 10}},
        [
            _target("A", [{"sku_code": "t1", "box_count": 100}], warehouse="JFK"),
            _target("B", [{"sku_code": "s2", "box_count": 100}], warehouse="NJ"),
        ],
    )
    assert res.scheduled_boxes(None, "JFK") == {"t1": 100}
    # s2's reservation is at NJ, so JFK's s2 is untouched.
    assert res.stock_shortages(None, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "s2", "box_count": 100}]}) == []


def test_internal_transfer_reserves_the_source_warehouse_only(monkeypatch):
    transfer = _target("A", [{"sku_code": "t1", "boxes_per_pallet": 10, "pallet_count": 5}], destination_warehouse="NJ")
    _stub_world(monkeypatch, {"t1": {10: 10}}, [transfer])
    assert res.scheduled_boxes(None, "JFK") == {"t1": 50}
    assert res.scheduled_boxes(None, "NJ") == {}


# ── confirm-time locked check ────────────────────────────────────────────────

def test_locked_check_uses_locked_rows_and_other_reservations(monkeypatch):
    _stub_world(monkeypatch, {}, [_target("A", [{"sku_code": "t1", "box_count": 60}])])
    rows = [SimpleNamespace(sku_code="t1", pallet_count=10, boxes_per_pallet=10)]
    locked = {}
    monkeypatch.setattr("core.uchoice_storage.acquire_storage_scopes", lambda db, scopes: locked.setdefault("scopes", scopes) and rows)

    refreshed = []
    db = SimpleNamespace(refresh=refreshed.append)
    [short] = res.locked_stock_shortages(db, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 50}]})

    assert locked["scopes"] == [("JFK", "t1")]
    # Locked rows are refreshed so a value cached before the wait can't be used.
    assert refreshed == rows
    assert (short.physical_boxes, short.scheduled_boxes, short.available_boxes) == (100, 60, 40)


def test_locked_check_passes_when_it_still_fits(monkeypatch):
    _stub_world(monkeypatch, {}, [_target("A", [{"sku_code": "t1", "box_count": 60}])])
    rows = [SimpleNamespace(sku_code="t1", pallet_count=10, boxes_per_pallet=10)]
    monkeypatch.setattr("core.uchoice_storage.acquire_storage_scopes", lambda db, scopes: rows)
    db = SimpleNamespace(refresh=lambda row: None)
    assert res.locked_stock_shortages(db, {"warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 40}]}) == []


# ── display ──────────────────────────────────────────────────────────────────

def test_no_reservation_means_no_position_and_no_line(monkeypatch):
    _stub_world(monkeypatch, {"t1": {36: 10}})
    assert res.schedule_positions(None, "JFK") == {}


def test_position_reports_scheduled_as_requested_and_pallets_left(monkeypatch):
    targets = [
        _target("A", [{"sku_code": "t1", "boxes_per_pallet": 36, "pallet_count": 3}]),
        _target("B", [{"sku_code": "t1", "box_count": 5}]),
    ]
    _stub_world(monkeypatch, {"t1": {36: 10}}, targets)

    pos = res.schedule_positions(None, "JFK")["t1"]

    assert (pos.scheduled_pallets, pos.scheduled_loose_boxes, pos.remaining_pallets) == (3, 5, 7)
    assert res.schedule_line(pos) == "待出库 3 托 + 5 箱（散），出库后剩 7 托（247 箱）"


def test_only_palletized_or_only_loose_lines_read_naturally(monkeypatch):
    _stub_world(monkeypatch, {"t1": {36: 10}}, [_target("A", [{"sku_code": "t1", "boxes_per_pallet": 36, "pallet_count": 3}])])
    assert res.schedule_line(res.schedule_positions(None, "JFK")["t1"]) == "待出库 3 托，出库后剩 7 托"

    _stub_world(monkeypatch, {"t1": {36: 10}}, [_target("A", [{"sku_code": "t1", "box_count": 5}])])
    assert res.schedule_line(res.schedule_positions(None, "JFK")["t1"]) == "待出库 5 箱（散），出库后剩 10 托（355 箱）"


def test_transfer_destination_credit_does_not_inflate_the_source(monkeypatch):
    transfer = _target("A", [{"sku_code": "t1", "boxes_per_pallet": 36, "pallet_count": 3}], destination_warehouse="NJ")
    _stub_world(monkeypatch, {"t1": {36: 10}}, [transfer])
    assert res.schedule_positions(None, "JFK")["t1"].remaining_pallets == 7


def test_physical_below_scheduled_flags_the_shortfall(monkeypatch):
    _stub_world(monkeypatch, {"t1": {36: 2}}, [_target("A", [{"sku_code": "t1", "box_count": 120}])])
    pos = res.schedule_positions(None, "JFK")["t1"]
    assert res.schedule_line(pos) == "⚠ 现有 72 箱，待出库 120 箱，缺口 48 箱"


def test_display_failure_never_breaks_the_reply(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(res, "scheduled_targets", boom)
    assert res.schedule_positions(None, "JFK") == {}


# ── messages ─────────────────────────────────────────────────────────────────

def test_shortage_text_names_held_stock_only_when_there_is_some():
    plain = res.Shortage("t1", 300, 240, 0)
    held = res.Shortage("t1", 300, 360, 120)
    assert res.shortage_text("T1", plain) == "T1：申请 300 箱，现有 240 箱"
    assert res.shortage_text("T1", held) == "T1：申请 300 箱，可用 240 箱（现有 360 箱，其中 120 箱已被其他待出库申请占用）"


def test_kefu_rejection_mentions_held_boxes(monkeypatch):
    from core import kefu_turn_apply

    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1 Tape"})
    reply = kefu_turn_apply._insufficient_stock_reply(None, "JFK", [res.Shortage("t1", 300, 360, 120)])
    assert "可用 240 箱" in reply and "现有 360 箱，其中 120 箱已被其他待出库申请占用" in reply
    assert kefu_turn_apply._insufficient_stock_reply(None, "JFK", []) is None


def test_kefu_rejection_is_unchanged_when_nothing_is_held(monkeypatch):
    from core import kefu_turn_apply

    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1 Tape"})
    reply = kefu_turn_apply._insufficient_stock_reply(None, "JFK", [res.Shortage("t1", 300, 240, 0)])
    assert "申请 300 箱，JFK仓现有 240 箱" in reply and "占用" not in reply


# ── Smart Robot: loose lines are now checked ─────────────────────────────────

def test_smart_robot_rejects_a_loose_line_beyond_available_stock(monkeypatch):
    from core import workflow_engine

    _stub_world(monkeypatch, {"t1": {10: 10}}, [_target("A", [{"sku_code": "t1", "box_count": 70}])])
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1 Tape"})
    sent, closed = [], []
    monkeypatch.setattr(workflow_engine, "send_message", lambda context, message: sent.append(message))
    monkeypatch.setattr(workflow_engine.session_manager, "close_session", lambda db, session, status: closed.append(status))
    session = SimpleNamespace(request_log_id=None, collected_fields={
        "warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 31}],
    })

    rejected = workflow_engine._reject_invalid_outbound_stock({}, session, None)

    assert rejected is True
    assert closed == ["cancelled"]
    assert "申请 31 箱，可用 30 箱" in sent[0]


def test_smart_robot_accepts_a_loose_line_within_available_stock(monkeypatch):
    from core import workflow_engine

    _stub_world(monkeypatch, {"t1": {10: 10}}, [_target("A", [{"sku_code": "t1", "box_count": 70}])])
    session = SimpleNamespace(request_log_id=None, collected_fields={
        "warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 30}],
    })
    assert workflow_engine._reject_invalid_outbound_stock({}, session, None) is False


# ── Kefu confirm-time check ──────────────────────────────────────────────────

def _confirm_world(monkeypatch, shortages):
    from core import kefu_turn_apply

    log = SimpleNamespace(log_id="log-1", status="pending")
    session = SimpleNamespace(status="pending_confirmation", collected_fields={
        "warehouse_code": "JFK", "sku_lines": [{"sku_code": "t1", "box_count": 50}],
    })
    seen = {}
    monkeypatch.setattr(kefu_turn_apply, "_load_log", lambda db, s: log)
    monkeypatch.setattr(kefu_turn_apply, "_set_context_for_session", lambda *a, **k: None)
    monkeypatch.setattr(kefu_turn_apply, "_append", lambda *a, **k: None)
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1 Tape"})

    def locked(db, fields, exclude_log_id=None):
        seen["exclude"] = exclude_log_id
        return shortages

    def finish(db, context, service, session_, log_):
        seen["finished"] = True
        return "ok"

    monkeypatch.setattr(res, "locked_stock_shortages", locked)
    monkeypatch.setattr(kefu_turn_apply, "_finish_execution", finish)
    return kefu_turn_apply, log, session, seen


def test_kefu_confirm_is_rejected_and_cancelled_when_stock_was_taken_meanwhile(monkeypatch):
    kefu_turn_apply, log, session, seen = _confirm_world(monkeypatch, [res.Shortage("t1", 50, 100, 60)])
    context = {"content": "确认"}

    reply = kefu_turn_apply.confirm_kefu_turn(None, context, {"name": "uchoice_outbound_request"}, session)

    assert "可用 40 箱" in reply and context["_reply"] == reply
    assert (log.status, session.status) == ("cancelled", "cancelled")
    assert seen["exclude"] == "log-1" and "finished" not in seen


def test_kefu_confirm_proceeds_to_processing_when_it_still_fits(monkeypatch):
    kefu_turn_apply, log, session, seen = _confirm_world(monkeypatch, [])

    reply = kefu_turn_apply.confirm_kefu_turn(None, {"content": "确认"}, {"name": "uchoice_outbound_request"}, session)

    assert reply == "ok" and log.status == "processing" and seen["finished"]


def test_other_services_skip_the_outbound_confirm_check(monkeypatch):
    kefu_turn_apply, log, session, seen = _confirm_world(monkeypatch, [res.Shortage("t1", 50, 100, 60)])

    reply = kefu_turn_apply.confirm_kefu_turn(None, {"content": "确认"}, {"name": "uchoice_inbound_request"}, session)

    assert reply == "ok" and "exclude" not in seen


# ── view_storage rendering ───────────────────────────────────────────────────

def test_schedule_note_is_added_only_to_skus_that_have_one():
    from core.result_message import _sku_bucket_sections

    rows = [
        {"sku_code": "t1", "boxes_per_pallet": 36, "pallet_count": 10},
        {"sku_code": "s2", "boxes_per_pallet": 20, "pallet_count": 2},
        {"sku_code": "s2", "boxes_per_pallet": 12, "pallet_count": 2},
        {"sku_code": "w1", "boxes_per_pallet": 5, "pallet_count": 1},
    ]
    labels = {"t1": "T1", "s2": "S2", "w1": "W1"}
    sections = _sku_bucket_sections(rows, labels, {"t1": "待出库 3 托，出库后剩 7 托", "s2": "待出库 1 托，出库后剩 3 托"})

    assert sections[0]["label"] == "S2（合计 4 托）"
    assert sections[0]["items"] == ["2 托 @ 12/托", "2 托 @ 20/托", "待出库 1 托，出库后剩 3 托"]
    assert sections[1]["items"] == ["T1：10 托 @ 36/托", "待出库 3 托，出库后剩 7 托"]
    assert sections[2]["items"] == ["W1：1 托 @ 5/托"]
    assert _sku_bucket_sections(rows, labels)[1]["items"] == ["T1：10 托 @ 36/托"]


def test_scheduled_sku_with_no_physical_pallets_still_shows_its_shortfall():
    from core.result_message import _sku_bucket_sections

    rows = [{"sku_code": "s2", "boxes_per_pallet": 20, "pallet_count": 2}]
    notes = {"t1": "⚠ 现有 0 箱，待出库 120 箱，缺口 120 箱"}
    sections = _sku_bucket_sections(rows, {"s2": "S2", "t1": "T1"}, notes)

    assert sections[0]["items"] == ["S2：2 托 @ 20/托"]
    assert sections[1]["items"] == ["T1：0 托", "⚠ 现有 0 箱，待出库 120 箱，缺口 120 箱"]


def test_summary_with_no_stock_but_scheduled_outbound_keeps_the_shortfall(monkeypatch):
    from core import result_message

    class Query:
        def filter_by(self, **kw): return self
        def filter(self, *a): return self
        def all(self): return []

    db = SimpleNamespace(query=lambda model: Query())
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1"})
    monkeypatch.setattr(result_message, "_schedule_notes", lambda db, wh: {"t1": "⚠ 现有 0 箱，待出库 120 箱，缺口 120 箱"})

    sections = result_message._warehouse_storage_summary_sections(db, "JFK", "仓当前库存")

    assert sections[1]["items"][-1] == "⚠ 现有 0 箱，待出库 120 箱，缺口 120 箱"
    monkeypatch.setattr(result_message, "_schedule_notes", lambda db, wh: {})
    assert result_message._warehouse_storage_summary_sections(db, "JFK", "仓当前库存")[0]["items"] == ["（无库存记录）"]


def test_view_storage_with_no_stock_but_scheduled_outbound_is_not_no_records(monkeypatch):
    from core import result_message

    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"t1": "T1"})
    monkeypatch.setattr(result_message, "_schedule_notes", lambda db, wh: {"t1": "⚠ 缺口"})
    context = {"result": {"storage_rows": []}, "collected_fields": {"warehouse_code": "JFK"}, "role": "customer"}

    sections = result_message._storage_sections_builder(context, None)

    assert sections[0]["label"].startswith("JFK 仓") and sections[1]["items"] == ["T1：0 托", "⚠ 缺口"]
    monkeypatch.setattr(result_message, "_schedule_notes", lambda db, wh: {})
    assert result_message._storage_sections_builder(context, None)[0]["items"] == ["（无匹配记录）"]
