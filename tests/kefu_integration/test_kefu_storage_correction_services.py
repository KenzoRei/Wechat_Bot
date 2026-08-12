"""
Real-Postgres, end-to-end verification that adjust_storage/recount_storage/
move_storage genuinely work through the Kefu-native pipeline (apply_kefu_
turn -> confirm_kefu_turn -> core/uchoice_storage.py's advisory-lock-
protected apply_storage_delta), not just "their wiring looks compatible."
These three were enabled in core.kefu_case_adapter._KEFU_ENABLED_SERVICES
on the strength of this file actually exercising a full new_request ->
confirmation -> confirm -> real storage mutation round trip for each.

Uses a synthetic warehouse code (never "JFK"/"DE") so this can never repeat
the incident where a hardcoded real warehouse+SKU in a test file destroyed
real production inventory via unscoped cleanup (see the fix to
tests/kefu_integration/test_kefu_address_pivot.py).
"""
import uuid

from sqlalchemy import text

from ai.base import AIResponse
from core.kefu_contracts import CaseTurnSuccess, KefuIdentity
from database import SessionLocal
import core.kefu_case_adapter as adapter

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"
WH = "TESTWHXCORR"


def _seed_staff():
    from models.group import GroupConfig
    from models.kefu import KefuStaff
    from models.role import Role
    db = SessionLocal()
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).one()
    role = db.query(Role).filter_by(name="admin").one()
    staff = KefuStaff(
        open_kfid=f"kf-corr-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-corr-{uuid.uuid4().hex[:8]}",
        group_id=group.group_id,
        role_id=role.role_id,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    result = staff.staff_id, staff.open_kfid, staff.external_userid
    db.close()
    return result


def _cleanup(staff_id):
    db = SessionLocal()
    db.execute(text("delete from case_turn where acting_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
    db.execute(text(
        "delete from case_execution where session_id=any("
        "select session_id from conversation_session where opened_by_staff_id=:staff)"
    ), {"staff": staff_id})
    db.execute(text("delete from request_log where submitted_by_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from conversation_session where opened_by_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from uchoice_storage_txn where warehouse_code=:wh"), {"wh": WH})
    db.execute(text("delete from uchoice_storage where warehouse_code=:wh"), {"wh": WH})
    db.commit()
    db.close()


def _confirm(processor, identity, case_number):
    return processor(
        identity=identity,
        message_content="确认",
        message_meta={"msgid": f"corr-confirm-{uuid.uuid4().hex[:8]}"},
        case_number_hint=case_number,
    )


def test_adjust_storage_end_to_end_via_kefu(monkeypatch):
    staff_id, open_kfid, external_userid = _seed_staff()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    try:
        db = SessionLocal()
        sku = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:wh,:sku,80,10)"
        ), {"wh": WH, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", reply="ready",
            extracted_fields={
                "warehouse_code": WH,
                "adjustment_lines": [{"sku_code": sku, "boxes_per_pallet": 80, "pallet_delta": -3, "reason": "damage"}],
            },
            all_fields_collected=True, service_type_name="adjust_storage",
        ))
        first = processor(
            identity=identity, message_content="adjust storage",
            message_meta={"msgid": f"corr-adjust-{uuid.uuid4().hex[:8]}"}, case_number_hint=None,
        )
        assert isinstance(first, CaseTurnSuccess)
        assert "请确认以下信息" in first.reply_text

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="confirm", reply="确认", extracted_fields={}, all_fields_collected=True, service_type_name=None,
        ))
        second = _confirm(processor, identity, first.case_number)
        assert isinstance(second, CaseTurnSuccess)

        db = SessionLocal()
        pallet_count = db.execute(text(
            "select pallet_count from uchoice_storage where warehouse_code=:wh and sku_code=:sku and boxes_per_pallet=80"
        ), {"wh": WH, "sku": sku}).scalar_one()
        txn_type = db.execute(text(
            "select txn_type from uchoice_storage_txn where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WH, "sku": sku}).scalar_one()
        db.close()
        assert pallet_count == 7  # 10 - 3
        assert txn_type == "adjust"
    finally:
        _cleanup(staff_id)


def test_recount_storage_end_to_end_via_kefu(monkeypatch):
    staff_id, open_kfid, external_userid = _seed_staff()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    try:
        db = SessionLocal()
        sku = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:wh,:sku,80,10)"
        ), {"wh": WH, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", reply="ready",
            extracted_fields={
                "warehouse_code": WH,
                "inventory_lines": [{"sku_code": sku, "boxes_per_pallet": 80, "pallet_count": 8}],
            },
            all_fields_collected=True, service_type_name="recount_storage",
        ))
        first = processor(
            identity=identity, message_content="recount",
            message_meta={"msgid": f"corr-recount-{uuid.uuid4().hex[:8]}"}, case_number_hint=None,
        )
        assert isinstance(first, CaseTurnSuccess)
        assert "请确认以下信息" in first.reply_text

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="confirm", reply="确认", extracted_fields={}, all_fields_collected=True, service_type_name=None,
        ))
        second = _confirm(processor, identity, first.case_number)
        assert isinstance(second, CaseTurnSuccess)

        db = SessionLocal()
        pallet_count = db.execute(text(
            "select pallet_count from uchoice_storage where warehouse_code=:wh and sku_code=:sku and boxes_per_pallet=80"
        ), {"wh": WH, "sku": sku}).scalar_one()
        txn_type = db.execute(text(
            "select txn_type from uchoice_storage_txn where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WH, "sku": sku}).scalar_one()
        db.close()
        assert pallet_count == 8
        assert txn_type == "recount"
    finally:
        _cleanup(staff_id)


def test_move_storage_end_to_end_via_kefu(monkeypatch):
    staff_id, open_kfid, external_userid = _seed_staff()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    try:
        db = SessionLocal()
        sku = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values "
            "(:wh,:sku,80,2), (:wh,:sku,64,1)"
        ), {"wh": WH, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", reply="ready",
            extracted_fields={
                "warehouse_code": WH,
                "move_lines": [{"sku_code": sku, "source_boxes_per_pallet": 80, "box_count_moved": 10, "target_boxes_per_pallet": 64}],
            },
            all_fields_collected=True, service_type_name="move_storage",
        ))
        first = processor(
            identity=identity, message_content="move storage",
            message_meta={"msgid": f"corr-move-{uuid.uuid4().hex[:8]}"}, case_number_hint=None,
        )
        assert isinstance(first, CaseTurnSuccess)
        assert "请确认以下信息" in first.reply_text

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="confirm", reply="确认", extracted_fields={}, all_fields_collected=True, service_type_name=None,
        ))
        second = _confirm(processor, identity, first.case_number)
        assert isinstance(second, CaseTurnSuccess)

        db = SessionLocal()
        buckets = dict(db.execute(text(
            "select boxes_per_pallet, pallet_count from uchoice_storage where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WH, "sku": sku}).all())
        db.close()
        assert buckets.get(80) == 1     # 2 - 1
        assert buckets.get(70) == 1     # new bucket: 80 - 10 boxes moved
        assert buckets.get(64) == 0     # 1 - 1
        assert buckets.get(74) == 1     # new bucket: 64 + 10 boxes received
    finally:
        _cleanup(staff_id)


def test_move_storage_allows_moving_an_entire_pallet_with_no_leftover_bucket(monkeypatch):
    """Live incident: box_count_moved == source_boxes_per_pallet (moving an
    entire pallet's worth away) used to be rejected outright. Now allowed,
    and must not leave a nonsensical boxes_per_pallet=0 row behind."""
    staff_id, open_kfid, external_userid = _seed_staff()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    try:
        db = SessionLocal()
        sku = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values "
            "(:wh,:sku,80,2), (:wh,:sku,64,1)"
        ), {"wh": WH, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", reply="ready",
            extracted_fields={
                "warehouse_code": WH,
                "move_lines": [{"sku_code": sku, "source_boxes_per_pallet": 80, "box_count_moved": 80, "target_boxes_per_pallet": 64}],
            },
            all_fields_collected=True, service_type_name="move_storage",
        ))
        first = processor(
            identity=identity, message_content="move entire pallet",
            message_meta={"msgid": f"corr-move-full-{uuid.uuid4().hex[:8]}"}, case_number_hint=None,
        )
        assert isinstance(first, CaseTurnSuccess)
        assert "请确认以下信息" in first.reply_text

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="confirm", reply="确认", extracted_fields={}, all_fields_collected=True, service_type_name=None,
        ))
        second = _confirm(processor, identity, first.case_number)
        assert isinstance(second, CaseTurnSuccess)

        db = SessionLocal()
        buckets = dict(db.execute(text(
            "select boxes_per_pallet, pallet_count from uchoice_storage where warehouse_code=:wh and sku_code=:sku"
        ), {"wh": WH, "sku": sku}).all())
        db.close()
        assert buckets.get(80) == 1      # 2 - 1
        assert 0 not in buckets          # no leftover bucket -- whole pallet moved
        assert buckets.get(64) == 0      # 1 - 1
        assert buckets.get(144) == 1     # new bucket: 64 + 80 boxes received
    finally:
        _cleanup(staff_id)


def test_move_storage_rejects_nonexistent_source_bucket_before_confirmation(monkeypatch):
    """Live incident: confirming a move from a source pallet spec that
    doesn't actually exist reached apply_storage_delta's own balance check
    at execution time, producing a confusing "库存不足" failure after
    confirmation had already been shown. Must be caught before that, with
    a message naming what buckets actually do exist."""
    staff_id, open_kfid, external_userid = _seed_staff()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    try:
        db = SessionLocal()
        sku = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:wh,:sku,64,10)"
        ), {"wh": WH, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", reply="ready",
            extracted_fields={
                "warehouse_code": WH,
                # 46/托 was never seeded -- only 64/托 exists.
                "move_lines": [{"sku_code": sku, "source_boxes_per_pallet": 46, "box_count_moved": 23, "target_boxes_per_pallet": 64}],
            },
            all_fields_collected=True, service_type_name="move_storage",
        ))
        result = processor(
            identity=identity, message_content="move from nonexistent bucket",
            message_meta={"msgid": f"corr-move-bad-{uuid.uuid4().hex[:8]}"}, case_number_hint=None,
        )
        assert isinstance(result, CaseTurnSuccess)
        assert "请确认以下信息" not in result.reply_text  # never reaches confirmation
        assert "没有 46/托" in result.reply_text
        assert "64/托" in result.reply_text  # tells them what actually exists

        db = SessionLocal()
        bucket = db.execute(text(
            "select pallet_count from uchoice_storage where warehouse_code=:wh and sku_code=:sku and boxes_per_pallet=64"
        ), {"wh": WH, "sku": sku}).scalar_one()
        db.close()
        assert bucket == 10  # untouched
    finally:
        _cleanup(staff_id)
