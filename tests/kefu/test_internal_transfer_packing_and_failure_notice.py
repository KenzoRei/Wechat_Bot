"""
Regression for the stuck 确认 on a loose-box internal transfer
(REQ-20261117-000291): the confirmation prompt used to appear without
destination packing, then execution failed and the sender got no reply.
"""
from types import SimpleNamespace

from core import access_control, kefu_delivery, kefu_sync, pre_confirm_validators, uchoice_context

SERIAL = "REQ-20261117-000291"


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter_by(self, **_):
        return self

    def first(self):
        return self._row


class _FakeDb:
    def __init__(self, address):
        self._address = address
        self.committed = self.rolled_back = self.closed = False

    def query(self, _model):
        return _FakeQuery(self._address)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _setup(monkeypatch, *, original_fields, destination="NJ"):
    monkeypatch.setattr(
        uchoice_context, "resolve_completion_target",
        lambda db, serial: (SimpleNamespace(), original_fields),
    )
    monkeypatch.setattr(uchoice_context, "sku_label_map", lambda db: {"s2": "S2 1500 ft Stretch Wrap"})
    address = SimpleNamespace(destination_warehouse_code=destination) if destination else None
    return _FakeDb(address)


def _run(db, collected):
    return pre_confirm_validators.run("confirm_outbound_completion", {}, collected, db)


def test_loose_internal_transfer_asks_for_destination_packing(monkeypatch):
    db = _setup(monkeypatch, original_fields={
        "destination_address_id": "a1", "sku_lines": [{"sku_code": "s2", "box_count": 50}],
        "warehouse_code": "DE",
    })
    # Picks already resolved (as _resolve_outbound_completion_loose_picks does), so only
    # the packing question can block.
    collected = {
        "reference_serial": SERIAL,
        "fulfillment_lines": [{"sku_code": "s2", "picks": [{"source_boxes_per_pallet": 36, "box_count": 50}]}],
    }
    msg = pre_confirm_validators._internal_transfer_packing_required({}, collected, db)
    assert msg and "NJ" in msg and "S2 1500 ft Stretch Wrap" in msg


def test_destination_packing_present_passes(monkeypatch):
    db = _setup(monkeypatch, original_fields={
        "destination_address_id": "a1", "sku_lines": [{"sku_code": "s2", "box_count": 50}],
    })
    collected = {
        "reference_serial": SERIAL,
        "destination_packing_lines": [{"sku_code": "s2", "boxes_per_pallet": 50, "pallet_count": 1}],
    }
    assert pre_confirm_validators._internal_transfer_packing_required({}, collected, db) is None


def test_whole_pallet_transfer_not_asked(monkeypatch):
    db = _setup(monkeypatch, original_fields={
        "destination_address_id": "a1",
        "sku_lines": [{"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 1}],
    })
    assert pre_confirm_validators._internal_transfer_packing_required(
        {}, {"reference_serial": SERIAL}, db) is None


def test_external_destination_not_asked(monkeypatch):
    db = _setup(monkeypatch, destination=None, original_fields={
        "destination_address_id": "a1", "sku_lines": [{"sku_code": "s2", "box_count": 50}],
    })
    assert pre_confirm_validators._internal_transfer_packing_required(
        {}, {"reference_serial": SERIAL}, db) is None


def test_failed_turn_queues_reply_to_sender(monkeypatch):
    sent = []
    monkeypatch.setattr(access_control, "check_kefu_access", lambda db, kf, ext: SimpleNamespace(staff_id="staff-1"))
    monkeypatch.setattr(kefu_delivery, "enqueue_text", lambda db, **kw: sent.append(kw))
    db = _FakeDb(None)
    turn = SimpleNamespace(msgid="m1", identity=SimpleNamespace(open_kfid="k", external_userid="u"))

    assert kefu_sync.notify_turn_failed(lambda: db, turn) is True
    assert sent[0]["recipient_staff_id"] == "staff-1"
    assert sent[0]["text_content"] == kefu_sync.TURN_FAILED_REPLY
    assert sent[0]["idempotency_key"] == "kefu-turn-failed:m1"
    assert db.committed and db.closed


def test_failed_turn_notice_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(access_control, "check_kefu_access", boom)
    db = _FakeDb(None)
    turn = SimpleNamespace(msgid="m2", identity=SimpleNamespace(open_kfid="k", external_userid="u"))
    assert kefu_sync.notify_turn_failed(lambda: db, turn) is False
    assert db.rolled_back and db.closed
