"""
Batch completion (confirm_*_completion_batch) against real PostgreSQL.
Plan: docs/reviews/active/2026-09-batch-completion-confirmation/plan.md --
test numbers in the docstrings refer to its Tests section.

Isolation: every test uses its own synthetic warehouse codes and a
synthetic SKU, so real JFK/NJ/DE inventory is never touched. Cleanup is by
the exact IDs/codes each test created, never a bulk delete by status/role.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from ai.base import AIResponse
from core import completion_batch as cb
from core.kefu_contracts import KefuIdentity
from database import SessionLocal
from handlers.uchoice.complete_batch import RunCompletionBatchHandler
from models.group import GroupConfig
from models.request_log import RequestLog
from models.session import ConversationSession
from models.service import ServiceType


class World:
    """Creates synthetic fixtures and remembers exactly what to delete."""

    def __init__(self):
        self.tag = uuid.uuid4().hex[:6]
        self.warehouses: list[str] = []
        self.skus: list[str] = []
        self.log_ids: list = []
        self.session_ids: list = []
        self.address_ids: list = []
        self.staff_ids: list = []
        self._clock = datetime.now(timezone.utc) - timedelta(days=1)

    def warehouse(self, prefix="W") -> str:
        code = f"B{prefix}{self.tag}"
        self.warehouses.append(code)
        return code

    def sku(self, db) -> str:
        code = f"bt{uuid.uuid4().hex[:8]}"
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:s,'Batch test SKU')"), {"s": code})
        self.skus.append(code)
        return code

    def group_id(self, db):
        return db.query(GroupConfig).order_by(GroupConfig.created_at).first().group_id

    def request(self, db, warehouse, sku_lines, destination_address_id=None, direction="outbound", staff_id=None) -> RequestLog:
        service_name = "uchoice_outbound_request" if direction == "outbound" else "uchoice_inbound_request"
        service = db.query(ServiceType).filter_by(name=service_name).one()
        fields = {"warehouse_code": warehouse, "sku_lines": sku_lines}
        if destination_address_id:
            fields["destination_address_id"] = str(destination_address_id)
        group_id = self.group_id(db)
        original = ConversationSession(
            wechat_openid=None, group_id=group_id, service_type_id=service.service_type_id,
            status="completed", conversation_history=[], collected_fields=fields,
            source_channel="kefu", opened_by_staff_id=staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(original)
        db.flush()
        # Explicit, strictly increasing created_at: candidate lists order by
        # it, and rows inserted in one transaction would otherwise tie.
        self._clock += timedelta(seconds=1)
        log = RequestLog(
            wechat_openid=None, group_id=group_id, service_type_id=service.service_type_id,
            status="processing", raw_message="batch test", source_channel="kefu",
            submitted_by_staff_id=staff_id, origin_session_id=original.session_id,
            created_at=self._clock,
        )
        db.add(log)
        db.flush()
        original.request_log_id = log.log_id
        self.session_ids.append(original.session_id)
        self.log_ids.append(log.log_id)
        return log

    def transfer_address(self, db, origin, destination):
        from models.uchoice import UchoiceAddress
        addr = UchoiceAddress(
            company_name=f"{destination} Warehouse", charge_type="truck_transfer",
            addr=f"batch test transfer {self.tag}", warehouse_code=origin,
            created_by="test_completion_batch", destination_warehouse_code=destination,
        )
        db.add(addr)
        db.flush()
        self.address_ids.append(addr.address_id)
        return addr.address_id

    def cleanup(self):
        db = SessionLocal()
        try:
            if self.staff_ids:
                sessions = db.execute(text(
                    "select session_id from conversation_session where opened_by_staff_id=any(:staff)"
                ), {"staff": self.staff_ids}).scalars().all()
                db.execute(text("delete from case_turn where acting_staff_id=any(:staff) or session_id=any(:s)"),
                           {"staff": self.staff_ids, "s": sessions})
                db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=any(:staff)"), {"staff": self.staff_ids})
                db.execute(text("delete from case_execution where session_id=any(:s)"), {"s": sessions})
                db.execute(text("delete from request_log where submitted_by_staff_id=any(:staff)"), {"staff": self.staff_ids})
                db.execute(text("delete from request_log where origin_session_id=any(:s)"), {"s": sessions})
                db.execute(text("delete from kefu_staff_case_context where staff_id=any(:staff)"), {"staff": self.staff_ids})
                db.execute(text("update conversation_session set request_log_id=null where session_id=any(:s)"), {"s": sessions})
                db.execute(text("delete from conversation_session where session_id=any(:s)"), {"s": sessions})
            if self.warehouses:
                db.execute(text("delete from uchoice_storage_txn where warehouse_code=any(:w)"), {"w": self.warehouses})
                db.execute(text("delete from uchoice_storage where warehouse_code=any(:w)"), {"w": self.warehouses})
            if self.log_ids:
                db.execute(text("update conversation_session set request_log_id=null where request_log_id=any(:l)"), {"l": self.log_ids})
                db.execute(text("delete from request_log where log_id=any(:l)"), {"l": self.log_ids})
            if self.session_ids:
                db.execute(text("delete from conversation_session where session_id=any(:s)"), {"s": self.session_ids})
            if self.address_ids:
                db.execute(text("delete from uchoice_address where address_id=any(:a)"), {"a": self.address_ids})
            if self.staff_ids:
                db.execute(text("delete from kefu_staff where staff_id=any(:staff)"), {"staff": self.staff_ids})
            if self.skus:
                db.execute(text("delete from uchoice_storage_txn where sku_code=any(:k)"), {"k": self.skus})
                db.execute(text("delete from uchoice_storage where sku_code=any(:k)"), {"k": self.skus})
                db.execute(text("delete from uchoice_sku where sku_code=any(:k)"), {"k": self.skus})
            db.commit()
        finally:
            db.close()


@pytest.fixture
def world():
    w = World()
    yield w
    w.cleanup()


def seed(db, warehouse, sku, buckets: dict[int, int]):
    for bpp, count in buckets.items():
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:w,:s,:b,:c)"
        ), {"w": warehouse, "s": sku, "b": bpp, "c": count})


def storage(db, warehouse, sku) -> dict[int, int]:
    return dict(db.execute(text(
        "select boxes_per_pallet,pallet_count from uchoice_storage "
        "where warehouse_code=:w and sku_code=:s and pallet_count<>0"
    ), {"w": warehouse, "s": sku}).all())


def status(db, log) -> str:
    log_id = getattr(log, "log_id", log)
    return db.execute(text("select status from request_log where log_id=:id"), {"id": log_id}).scalar_one()


def pallets(bpp, count, sku):
    return [{"sku_code": sku, "boxes_per_pallet": bpp, "pallet_count": count}]


def prepare_fields(db, serials, direction="outbound") -> dict:
    prepared = cb.prepare_batch(db, serials, direction)
    return {
        "reference_serials": prepared.serials,
        "_preview_picks": prepared.preview_picks,
        "_batch_notes": prepared.notes,
        "_batch_direction": direction,
    }


def run_batch(db, fields, indices=None, direction="outbound") -> dict:
    context = {
        "source_channel": "kefu", "wechat_openid": None, "submitted_by_staff_id": "test-batch",
        "role": "admin", "warehouse_codes": None, "group_id": None,
        "collected_fields": fields, "result": {},
    }
    if indices is not None:
        context["_batch_confirm_indices"] = indices
    result = RunCompletionBatchHandler().handle(context, {"direction": direction}, db)
    db.commit()
    return result


# ── Execution ────────────────────────────────────────────────────────────────

def test_full_batch_completes_every_request_and_storage_matches_simulation(world):
    """Test 19 (simulation == execution) on the happy path."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {72: 3})
        r1 = world.request(db, w, pallets(72, 1, sku))
        r2 = world.request(db, w, pallets(72, 1, sku))
        db.commit()

        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])
        expected = cb.simulate_allocation(
            cb.load_buckets(db, [(w, sku)]), [cb.load_target(db, s) for s in fields["reference_serials"]]
        ).final_buckets[(w, sku)]
        result = run_batch(db, fields)

        assert [e["serial_number"] for e in result["batch_completed"]] == [r1.serial_number, r2.serial_number]
        assert status(db, r1) == status(db, r2) == "success"
        assert storage(db, w, sku) == expected == {72: 1}
        stored = db.execute(text("select result from request_log where log_id=:id"), {"id": r1.log_id}).scalar_one()
        assert stored["warehouse_code"] == w  # kefu_completion_notice filters on it
    finally:
        db.close()


def test_blocked_target_rolls_back_the_whole_batch(world):
    """Test 5: ② cancelled between summary and 确认 -> nothing applied."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {72: 3})
        r1 = world.request(db, w, pallets(72, 1, sku))
        r2 = world.request(db, w, pallets(72, 1, sku))
        db.commit()
        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])
        db.execute(text("update request_log set status='cancelled' where log_id=:id"), {"id": r2.log_id})
        db.commit()

        result = run_batch(db, fields)

        assert result["_kefu_stop_workflow"] == "batch_blocked"
        assert result["batch_blocked"]["kind"] == "rejected"
        assert result["batch_blocked"]["serials"] == [r2.serial_number]
        assert status(db, r1) == "processing"
        assert storage(db, w, sku) == {72: 3}
        assert db.execute(text("select count(*) from uchoice_storage_txn where warehouse_code=:w"), {"w": w}).scalar() == 0
    finally:
        db.close()


def test_partial_confirm_completes_only_the_chosen_requests(world):
    """Test 6."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {72: 3})
        rs = [world.request(db, w, pallets(72, 1, sku)) for _ in range(3)]
        db.commit()
        fields = prepare_fields(db, [r.serial_number for r in rs])

        result = run_batch(db, fields, indices=[1, 3])

        assert [e["serial_number"] for e in result["batch_completed"]] == [rs[0].serial_number, rs[2].serial_number]
        assert [status(db, r) for r in rs] == ["success", "processing", "success"]
        assert storage(db, w, sku) == {72: 1}
    finally:
        db.close()


def test_subset_whose_picks_depend_on_an_unconfirmed_request_rerenders(world):
    """Test 11: one 100-box pallet; ① takes 40, ② takes 30. ②'s preview
    draws on the 60-box leftover ① would create -- confirming ② alone must
    not execute unseen picks."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {100: 1})
        r1 = world.request(db, w, [{"sku_code": sku, "box_count": 40}])
        r2 = world.request(db, w, [{"sku_code": sku, "box_count": 30}])
        db.commit()
        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])
        assert fields["_preview_picks"][r2.serial_number] == {sku: [{"source_boxes_per_pallet": 60, "box_count": 30}]}

        result = run_batch(db, fields, indices=[2])
        assert result["_kefu_stop_workflow"] == "batch_blocked"
        assert result["batch_blocked"]["kind"] == "picks_changed"
        assert storage(db, w, sku) == {100: 1}
        assert status(db, r2) == "processing"

        # Re-rendered for just ② -> new preview -> executes as shown.
        fields = prepare_fields(db, [r2.serial_number])
        assert fields["_preview_picks"][r2.serial_number] == {sku: [{"source_boxes_per_pallet": 100, "box_count": 30}]}
        run_batch(db, fields)
        assert status(db, r2) == "success"
        assert storage(db, w, sku) == {70: 1}
    finally:
        db.close()


def test_reverse_display_order_simulates_and_executes_identically(world):
    """Test 11, last bullet: mutation order is display order, not log_id."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {100: 1})
        r1 = world.request(db, w, [{"sku_code": sku, "box_count": 40}])
        r2 = world.request(db, w, [{"sku_code": sku, "box_count": 30}])
        db.commit()
        fields = prepare_fields(db, [r2.serial_number, r1.serial_number])
        assert fields["_preview_picks"][r1.serial_number] == {sku: [{"source_boxes_per_pallet": 70, "box_count": 40}]}

        run_batch(db, fields)
        assert status(db, r1) == status(db, r2) == "success"
        assert storage(db, w, sku) == {30: 1}
    finally:
        db.close()


def test_whole_pallet_line_split_is_previewed_and_executed_as_shown(world):
    """Test 12: "1 托 @ 72/托" with 36/托 x2 + 72/托 x1 draws from the 36s."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {36: 2, 72: 1})
        r1 = world.request(db, w, pallets(72, 1, sku))
        r2 = world.request(db, w, pallets(72, 1, sku))
        db.commit()
        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])
        assert fields["_preview_picks"][r1.serial_number] == {sku: [{"source_boxes_per_pallet": 36, "box_count": 72}]}

        from core.confirmation import build_confirmation_sections
        rendered = "\n".join(
            str(i) for s in build_confirmation_sections("confirm_outbound_completion_batch", fields, db) for i in s["items"]
        )
        assert "取货（系统预计）：72箱@36/托" in rendered

        run_batch(db, fields)
        assert storage(db, w, sku) == {}
        picks = db.execute(text("select result from request_log where log_id=:id"), {"id": r1.log_id}).scalar_one()["source_picks"]
        assert picks == [{"sku_code": sku, "picks": [{"source_boxes_per_pallet": 36, "box_count": 72}]}]
    finally:
        db.close()


def test_full_confirm_rerenders_when_stock_moved_but_stayed_sufficient(world):
    """Test 17 (round-2 finding 2): no shortage, but different buckets."""
    from core.uchoice_storage import apply_storage_delta
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {36: 2, 72: 1})
        r1 = world.request(db, w, pallets(72, 1, sku))
        db.commit()
        fields = prepare_fields(db, [r1.serial_number])
        assert fields["_preview_picks"][r1.serial_number] == {sku: [{"source_boxes_per_pallet": 36, "box_count": 72}]}

        apply_storage_delta(db, w, sku, 36, -2, "adjust", None, note="test", created_by="test-batch")
        db.commit()

        result = run_batch(db, fields)
        assert result["batch_blocked"]["kind"] == "picks_changed"
        assert status(db, r1) == "processing"
        assert storage(db, w, sku) == {72: 1}
    finally:
        db.close()


def test_chained_transfers_credit_destination_with_original_packing(world):
    """Test 16 (round-2 finding 1): ① A->B sourced from 2x36 but packed as
    1x72; ② ships 1x72 out of B, which only ①'s transfer covers."""
    db = SessionLocal()
    try:
        a, b, sku = world.warehouse("A"), world.warehouse("B"), world.sku(db)
        seed(db, a, sku, {36: 2})
        addr = world.transfer_address(db, a, b)
        r1 = world.request(db, a, pallets(72, 1, sku), destination_address_id=addr)
        r2 = world.request(db, b, pallets(72, 1, sku))
        db.commit()

        alone = cb.prepare_batch(db, [r2.serial_number], "outbound")
        assert alone.serials == [] and "库存不足" in alone.notes[0]

        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])
        assert fields["reference_serials"] == [r1.serial_number, r2.serial_number]
        assert fields["_preview_picks"][r2.serial_number] == {sku: [{"source_boxes_per_pallet": 72, "box_count": 72}]}
        targets = [cb.load_target(db, s) for s in fields["reference_serials"]]
        expected = cb.simulate_allocation(cb.load_buckets(db, cb.storage_scopes(targets)), targets).final_buckets

        run_batch(db, fields)
        assert status(db, r1) == status(db, r2) == "success"
        assert storage(db, a, sku) == expected[(a, sku)] == {}
        assert storage(db, b, sku) == expected[(b, sku)] == {}
        txn_types = db.execute(text(
            "select txn_type from uchoice_storage_txn where warehouse_code=:b and sku_code=:s order by txn_type"
        ), {"b": b, "s": sku}).scalars().all()
        assert "transfer_in" in txn_types
    finally:
        db.close()


def test_requests_needing_packing_input_are_excluded(world):
    """Test 13: loose inbound and loose internal transfer never reach execution."""
    db = SessionLocal()
    try:
        a, b, sku = world.warehouse("A"), world.warehouse("B"), world.sku(db)
        seed(db, a, sku, {80: 2})
        addr = world.transfer_address(db, a, b)
        loose_inbound = world.request(db, a, [{"sku_code": sku, "box_count": 20}], direction="inbound")
        whole_inbound = world.request(db, a, pallets(40, 1, sku), direction="inbound")
        loose_transfer = world.request(db, a, [{"sku_code": sku, "box_count": 20}], destination_address_id=addr)
        db.commit()

        inbound = cb.prepare_batch(db, [loose_inbound.serial_number, whole_inbound.serial_number], "inbound")
        assert inbound.serials == [whole_inbound.serial_number]
        assert any(loose_inbound.serial_number in n and "散箱" in n for n in inbound.notes)

        outbound = cb.prepare_batch(db, [loose_transfer.serial_number], "outbound")
        assert outbound.serials == []
        assert "散箱" in outbound.notes[0]

        run_batch(db, prepare_fields(db, [whole_inbound.serial_number], "inbound"), direction="inbound")
        assert status(db, whole_inbound) == "success"
        assert storage(db, a, sku) == {80: 2, 40: 1}
    finally:
        db.close()


def test_unexpected_handler_failure_rolls_back_and_marks_nothing_failed(world, monkeypatch):
    """Test 15."""
    from handlers.uchoice import storage_txns
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {72: 3})
        r1 = world.request(db, w, pallets(72, 1, sku))
        r2 = world.request(db, w, pallets(72, 1, sku))
        db.commit()
        fields = prepare_fields(db, [r1.serial_number, r2.serial_number])

        original = storage_txns.ApplyOutboundStorageHandler.handle
        calls = []

        def flaky(self, context, config, db_):
            calls.append(context["collected_fields"]["reference_serial"])
            if len(calls) == 2:
                raise RuntimeError("simulated unexpected failure")
            return original(self, context, config, db_)

        monkeypatch.setattr(storage_txns.ApplyOutboundStorageHandler, "handle", flaky)
        result = run_batch(db, fields)

        assert result == {"_kefu_stop_workflow": "batch_failed"}
        assert status(db, r1) == status(db, r2) == "processing"
        assert storage(db, w, sku) == {72: 3}
    finally:
        db.close()


def test_concurrent_batches_sharing_skus_in_opposite_order_do_not_deadlock(world):
    """Test 10 (review finding 1): X = [A on p, B on q], Y = [C on q, D on p],
    plus a single completion racing on p."""
    from handlers.uchoice.lookup_validate import LookupAndValidateCompletionHandler
    from handlers.uchoice.storage_txns import ApplyOutboundStorageHandler

    db = SessionLocal()
    try:
        w = world.warehouse()
        p, q = world.sku(db), world.sku(db)
        seed(db, w, p, {10: 10})
        seed(db, w, q, {10: 10})
        a = world.request(db, w, pallets(10, 1, p))
        b = world.request(db, w, pallets(10, 1, q))
        c = world.request(db, w, pallets(10, 1, q))
        d = world.request(db, w, pallets(10, 1, p))
        single = world.request(db, w, pallets(10, 1, p))
        db.commit()
        x_fields = prepare_fields(db, [a.serial_number, b.serial_number])
        y_fields = prepare_fields(db, [c.serial_number, d.serial_number])
        single_log_id = single.log_id
    finally:
        db.close()

    barrier = threading.Barrier(3)
    errors, results = [], []

    def batch_worker(fields):
        wdb = SessionLocal()
        try:
            barrier.wait(timeout=10)
            results.append(run_batch(wdb, fields))
        except Exception as exc:
            wdb.rollback()
            errors.append(exc)
        finally:
            wdb.close()

    def single_worker():
        wdb = SessionLocal()
        try:
            context = {
                "source_channel": "kefu", "wechat_openid": None, "submitted_by_staff_id": "test-batch",
                "role": "admin", "warehouse_codes": None, "request_log_id": str(single_log_id),
                "collected_fields": {}, "result": {},
            }
            barrier.wait(timeout=10)
            LookupAndValidateCompletionHandler().handle(context, {"direction": "outbound"}, wdb)
            ApplyOutboundStorageHandler().handle(context, {}, wdb)
            wdb.execute(text("update request_log set status='success' where log_id=:id"), {"id": single_log_id})
            wdb.commit()
        except Exception as exc:
            wdb.rollback()
            errors.append(exc)
        finally:
            wdb.close()

    threads = [
        threading.Thread(target=batch_worker, args=(x_fields,)),
        threading.Thread(target=batch_worker, args=(y_fields,)),
        threading.Thread(target=single_worker),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)
    assert errors == []
    assert all("batch_completed" in r for r in results)

    db = SessionLocal()
    try:
        assert [status(db, r) for r in (a, b, c, d, single)] == ["success"] * 5
        assert storage(db, w, p) == {10: 7}
        assert storage(db, w, q) == {10: 8}
    finally:
        db.close()


def test_overlapping_batches_one_wins_the_other_is_blocked_cleanly(world):
    """Test 8: two batches over the same targets -- exactly one applies;
    the loser stops with nothing applied and no deadlock."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {10: 10})
        rs = [world.request(db, w, pallets(10, 1, sku)) for _ in range(2)]
        db.commit()
        serials = [r.serial_number for r in rs]
        log_ids = [r.log_id for r in rs]
        fields_a = prepare_fields(db, serials)
        fields_b = prepare_fields(db, list(reversed(serials)))
    finally:
        db.close()

    barrier = threading.Barrier(2)
    errors, results = [], []

    def worker(fields):
        wdb = SessionLocal()
        try:
            barrier.wait(timeout=10)
            results.append(run_batch(wdb, fields))
        except Exception as exc:
            wdb.rollback()
            errors.append(exc)
        finally:
            wdb.close()

    threads = [threading.Thread(target=worker, args=(f,)) for f in (fields_a, fields_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)
    assert errors == []
    assert sorted("batch_completed" in r for r in results) == [False, True]
    loser = next(r for r in results if "batch_completed" not in r)
    assert loser["batch_blocked"]["kind"] == "rejected"

    db = SessionLocal()
    try:
        assert [status(db, i) for i in log_ids] == ["success", "success"]
        assert storage(db, w, sku) == {10: 8}
    finally:
        db.close()


# ── Full Kefu turns ──────────────────────────────────────────────────────────

def _warehouseman(world, db, warehouse):
    from models.kefu import KefuStaff
    from models.role import Role
    role = db.query(Role).filter_by(name="warehouseman").one()
    staff = KefuStaff(
        open_kfid=f"kf-batch-{world.tag}", external_userid=f"batch-{uuid.uuid4().hex[:8]}",
        group_id=world.group_id(db), role_id=role.role_id, warehouse_codes=[warehouse],
    )
    db.add(staff)
    db.flush()
    world.staff_ids.append(staff.staff_id)
    return staff


def _ai(monkeypatch, responses):
    """Scripted AI; a turn the code must handle deterministically calls
    nothing, so an exhausted script fails the test loudly."""
    import core.kefu_case_adapter as adapter
    script = iter(responses)
    monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(script))
    return adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)


def _turn(processor, identity, content, case_number=None):
    return processor(
        identity=identity, message_content=content,
        message_meta={"msgid": f"batch-{uuid.uuid4().hex}"}, case_number_hint=case_number,
    )


def test_fresh_all_shows_capped_snapshot_and_number_reply_executes_subset(world, monkeypatch):
    """Tests 18 + 6 end to end: fresh "全部确认出库" builds its own 9-item
    snapshot (with an overflow line), and "13" executes ① and ③ at once."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {10: 20})
        staff = _warehouseman(world, db, w)
        reqs = [world.request(db, w, pallets(10, 1, sku), staff_id=None) for _ in range(11)]
        db.commit()
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        serials = [r.serial_number for r in reqs]
    finally:
        db.close()

    processor = _ai(monkeypatch, [AIResponse(
        intent="new_request", service_type_name="confirm_outbound_completion_batch",
        extracted_fields={"selection": {"select_all": True}}, all_fields_collected=False, reply="",
    )])
    summary = _turn(processor, identity, "全部确认出库")
    assert "批量出库完成确认（共 9 笔）" in summary.reply_text
    assert "还有 2 笔未列出" in summary.reply_text
    assert f"① {serials[0]}" in summary.reply_text and f"⑨ {serials[8]}" in summary.reply_text
    assert serials[9] not in summary.reply_text.split("还有")[0]
    assert "部分确认" in summary.reply_text

    # A request created after the summary is never swept in.
    db = SessionLocal()
    try:
        late = world.request(db, w, pallets(10, 1, sku))
        db.commit()
        db.refresh(late)
    finally:
        db.close()

    done = _turn(processor, identity, "13", summary.case_number)
    assert "已完成 2 笔出库确认" in done.reply_text

    db = SessionLocal()
    try:
        statuses = [status(db, r) for r in reqs]
        assert statuses[0] == statuses[2] == "success"
        assert statuses.count("success") == 2
        assert status(db, late) == "processing"
        assert storage(db, w, sku) == {10: 18}
    finally:
        db.close()


def _two_request_setup(world):
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {10: 5})
        staff = _warehouseman(world, db, w)
        logs = [world.request(db, w, pallets(10, 1, sku)) for _ in range(3)]
        db.commit()
        return w, sku, KefuIdentity(staff.open_kfid, staff.external_userid), [(l.log_id, l.serial_number) for l in logs]
    finally:
        db.close()


def test_mixed_number_reply_at_summary_never_executes_the_batch(world, monkeypatch):
    """Audit rounds 3 and 4, P1: only an exact affirmative or a pure number
    reply (both matched in code) may execute a batch. Whatever the AI says
    about "①③，③少发了2箱", "确认，但第三笔少发两箱" (confirm + restated
    quantities) or "行吧就这样", nothing runs. A plain 确认 afterwards works."""
    w, sku, identity, reqs = _two_request_setup(world)
    confirm = AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=False, reply="")
    restated_lines = {"fulfillment_lines": [{"sku_code": sku, "boxes_per_pallet": 10, "pallet_count": 1}]}
    processor = _ai(monkeypatch, [
        AIResponse(intent="new_request", service_type_name="confirm_outbound_completion_batch",
                   extracted_fields={"selection": {"select_all": True}}, all_fields_collected=False, reply=""),
        confirm,   # the AI misreads the mixed reply as a plain confirm
        # Audit round 4: AI says confirm AND extracted changed quantities.
        AIResponse(intent="confirm", service_type_name=None, all_fields_collected=False, reply="",
                   extracted_fields=restated_lines),
        confirm,   # vague non-affirmative reply the AI calls confirm
    ])
    summary = _turn(processor, identity, "全部确认出库")
    assert "共 3 笔" in summary.reply_text

    mixed = _turn(processor, identity, "①③，③少发了2箱", summary.case_number)
    assert "未执行任何操作" in mixed.reply_text
    restated = _turn(processor, identity, "确认，但第三笔少发两箱", mixed.case_number)
    assert "单独确认" in restated.reply_text
    vague = _turn(processor, identity, "行吧就这样", restated.case_number)
    assert "未执行任何操作" in vague.reply_text and "回复 **确认** 提交全部" in vague.reply_text

    db = SessionLocal()
    try:
        assert [status(db, i) for i, _ in reqs] == ["processing"] * 3
        assert storage(db, w, sku) == {10: 5}
    finally:
        db.close()

    done = _turn(processor, identity, "确认", vague.case_number)  # exact affirmative: matched in code
    assert "已完成 3 笔出库确认" in done.reply_text


def test_unclear_fresh_selection_asks_instead_of_selecting_all(world, monkeypatch):
    """Audit round 3, P2: an empty selection ("确认前面几个") lists and asks;
    the numbered answer is then parsed in code, and "全部" works too."""
    w, sku, identity, reqs = _two_request_setup(world)
    processor = _ai(monkeypatch, [
        AIResponse(intent="new_request", service_type_name="confirm_outbound_completion_batch",
                   extracted_fields={}, all_fields_collected=False, reply=""),
    ])
    question = _turn(processor, identity, "确认前面几个出库")
    assert "请问要确认哪几笔出库申请" in question.reply_text
    assert "请确认以下信息" not in question.reply_text

    summary = _turn(processor, identity, "13", question.case_number)  # no AI call
    assert "共 2 笔" in summary.reply_text
    assert f"① {reqs[0][1]}" in summary.reply_text and f"② {reqs[2][1]}" in summary.reply_text
    assert reqs[1][1] not in summary.reply_text

    db = SessionLocal()
    try:
        assert [status(db, i) for i, _ in reqs] == ["processing"] * 3
    finally:
        db.close()


def test_single_list_pivots_to_batch_and_blocked_request_rerenders(world, monkeypatch):
    """Tests 14, 9 and decision D2 end to end: the single-completion list is
    capped at 9 with an overflow line, "1和3" pivots the same case into a
    batch, a request cancelled before 确认 re-renders the rest, and a second
    确认 completes it -- leaving no orphaned placeholder log."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        seed(db, w, sku, {10: 20})
        staff = _warehouseman(world, db, w)
        logs = [world.request(db, w, pallets(10, 1, sku)) for _ in range(11)]
        db.commit()
        reqs = [(log.log_id, log.serial_number) for log in logs]
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        staff_id = staff.staff_id
    finally:
        db.close()

    confirm = AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=False, reply="")
    processor = _ai(monkeypatch, [
        AIResponse(intent="new_request", service_type_name="confirm_outbound_completion",
                   extracted_fields={}, all_fields_collected=False, reply=""),
        confirm, confirm,
    ])

    listing = _turn(processor, identity, "确认出库")
    assert "请问是哪一条" in listing.reply_text
    assert "9. " in listing.reply_text and "10. " not in listing.reply_text
    assert "还有 2 笔未列出" in listing.reply_text
    assert "多条可一起确认" in listing.reply_text

    summary = _turn(processor, identity, "1和3", listing.case_number)  # deterministic, no AI call
    assert "批量出库完成确认（共 2 笔）" in summary.reply_text
    (first_id, first_serial), (third_id, third_serial) = reqs[0], reqs[2]
    assert f"① {first_serial}" in summary.reply_text
    assert f"② {third_serial}" in summary.reply_text

    db = SessionLocal()
    try:
        db.execute(text("update request_log set status='cancelled' where log_id=:id"), {"id": third_id})
        db.commit()
    finally:
        db.close()

    rerendered = _turn(processor, identity, "确认", summary.case_number)
    assert third_serial in rerendered.reply_text and "本次未执行任何操作" in rerendered.reply_text
    assert "请确认以下信息" in rerendered.reply_text
    db = SessionLocal()
    try:
        assert status(db, first_id) == "processing"
        assert storage(db, w, sku) == {10: 20}
    finally:
        db.close()

    done = _turn(processor, identity, "确认", rerendered.case_number)
    assert "已完成 1 笔出库确认" in done.reply_text

    db = SessionLocal()
    try:
        assert status(db, first_id) == "success"
        assert storage(db, w, sku) == {10: 19}
        orphaned = db.execute(text(
            "select count(*) from request_log where submitted_by_staff_id=:s and status='pending'"
        ), {"s": staff_id}).scalar()
        assert orphaned == 0
    finally:
        db.close()
