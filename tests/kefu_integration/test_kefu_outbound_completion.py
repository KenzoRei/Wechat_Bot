import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ai.base import AIResponse
from core.kefu_contracts import KefuIdentity
from database import SessionLocal
from models.kefu import KefuStaff
from models.request_log import RequestLog
from models.session import ConversationSession
import core.kefu_case_adapter as adapter


def test_kefu_outbound_completion_consumes_total_boxes_and_completes_target(monkeypatch):
    from models.group import GroupConfig
    from models.role import Role
    from models.service import ServiceType

    db = SessionLocal()
    staff_id = original_session_id = log_id = None
    sku = f"ks{uuid.uuid4().hex[:8]}"
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()
        staff = KefuStaff(
            open_kfid=f"kf-complete-{uuid.uuid4().hex[:8]}",
            external_userid=f"complete-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=role.role_id,
        )
        db.add(staff)
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:sku,'Kefu completion test')"), {"sku": sku})
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values ('JFK',:sku,40,2),('JFK',:sku,80,1)"
        ), {"sku": sku})
        db.flush()
        original = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="completed",
            conversation_history=[],
            collected_fields={
                "warehouse_code": "JFK",
                "sku_lines": [{"sku_code": sku, "boxes_per_pallet": 72, "pallet_count": 2}],
            },
            source_channel="kefu",
            opened_by_staff_id=staff.staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(original)
        db.flush()
        log = RequestLog(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="processing",
            raw_message="original outbound",
            source_channel="kefu",
            submitted_by_staff_id=staff.staff_id,
            origin_session_id=original.session_id,
        )
        db.add(log)
        db.flush()
        original.request_log_id = log.log_id
        db.commit()
        db.refresh(staff)
        db.refresh(original)
        db.refresh(log)
        staff_id, original_session_id, log_id = staff.staff_id, original.session_id, log.log_id
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        serial = log.serial_number
        db.close()

        responses = iter([
            AIResponse(
                intent="new_request", service_type_name="confirm_outbound_completion",
                extracted_fields={"reference_serial": serial}, all_fields_collected=True,
                reply="AI CONFIRMATION PROSE",
            ),
            AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=True, reply="confirm"),
        ])
        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(responses))
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        pending = processor(
            identity=identity, message_content=f"confirm {serial}",
            message_meta={"msgid": f"complete-open-{uuid.uuid4().hex}"}, case_number_hint=None,
        )
        assert "AI CONFIRMATION PROSE" not in pending.reply_text
        assert "确认以下信息" in pending.reply_text

        completed = processor(
            identity=identity, message_content="确认",
            message_meta={"msgid": f"complete-confirm-{uuid.uuid4().hex}"}, case_number_hint=pending.case_number,
        )
        assert completed.reply_text

        db = SessionLocal()
        assert db.execute(text("select status from request_log where log_id=:id"), {"id": log_id}).scalar_one() == "success"
        rows = db.execute(text(
            "select boxes_per_pallet,pallet_count from uchoice_storage where warehouse_code='JFK' and sku_code=:sku and pallet_count<>0"
        ), {"sku": sku}).all()
        assert rows == [(16, 1)]
    finally:
        try:
            db.rollback()
        except Exception:
            db = SessionLocal()
        if staff_id:
            sessions = db.execute(text("select session_id from conversation_session where opened_by_staff_id=:staff"), {"staff": staff_id}).scalars().all()
            db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {"staff": staff_id, "sessions": sessions})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from request_log where submitted_by_staff_id=:staff or log_id=:log"), {"staff": staff_id, "log": log_id})
            db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
        db.execute(text("delete from uchoice_storage_txn where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_storage where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_sku where sku_code=:sku"), {"sku": sku})
        db.commit()
        db.close()


def test_kefu_outbound_completion_auto_resolves_loose_box_picks(monkeypatch):
    """
    Live incident: an original outbound request for a loose (box_count)
    SKU line always failed confirm_outbound_completion with "当前库存不足以
    自动分配所需数量" regardless of actual stock -- the auto-pick resolution
    step (_resolve_outbound_loose_pick_defaults) existed only in
    core/workflow_engine.py's Smart Robot path and was never ported to
    core/kefu_turn_apply.py, so `fulfillment_lines[sku].picks` was always
    empty by the time pre_confirm_validators.py's _loose_outbound_pick_
    required ran (that validator itself never checks stock -- it only
    checks whether this resolution already happened). Real numbers from
    the incident: 524 real boxes on hand, only 15 needed, still rejected.
    """
    from models.group import GroupConfig
    from models.role import Role
    from models.service import ServiceType

    db = SessionLocal()
    staff_id = original_session_id = log_id = None
    sku = f"kl{uuid.uuid4().hex[:8]}"
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()
        staff = KefuStaff(
            open_kfid=f"kf-loose-{uuid.uuid4().hex[:8]}",
            external_userid=f"loose-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=role.role_id,
        )
        db.add(staff)
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:sku,'Kefu loose-pick test')"), {"sku": sku})
        # Mirrors the real incident's stock shape: two odd buckets totalling
        # far more than what's needed (104x1 + 105x4 = 524, only 15 needed).
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values ('JFK',:sku,104,1),('JFK',:sku,105,4)"
        ), {"sku": sku})
        db.flush()
        original = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="completed",
            conversation_history=[],
            collected_fields={
                "warehouse_code": "JFK",
                "sku_lines": [{"sku_code": sku, "box_count": 15}],
            },
            source_channel="kefu",
            opened_by_staff_id=staff.staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(original)
        db.flush()
        log = RequestLog(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="processing",
            raw_message="original outbound",
            source_channel="kefu",
            submitted_by_staff_id=staff.staff_id,
            origin_session_id=original.session_id,
        )
        db.add(log)
        db.flush()
        original.request_log_id = log.log_id
        db.commit()
        db.refresh(staff)
        db.refresh(original)
        db.refresh(log)
        staff_id, original_session_id, log_id = staff.staff_id, original.session_id, log.log_id
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        serial = log.serial_number
        db.close()

        responses = iter([
            AIResponse(
                intent="new_request", service_type_name="confirm_outbound_completion",
                extracted_fields={"reference_serial": serial}, all_fields_collected=True,
                reply="ready",
            ),
            AIResponse(intent="confirm", service_type_name=None, extracted_fields={}, all_fields_collected=True, reply="确认"),
        ])
        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(responses))
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        pending = processor(
            identity=identity, message_content=f"confirm {serial}",
            message_meta={"msgid": f"loose-open-{uuid.uuid4().hex}"}, case_number_hint=None,
        )
        # Must reach confirmation, not the "库存不足以自动分配" rejection.
        assert "确认以下信息" in pending.reply_text
        assert "库存不足" not in pending.reply_text

        completed = processor(
            identity=identity, message_content="确认",
            message_meta={"msgid": f"loose-confirm-{uuid.uuid4().hex}"}, case_number_hint=pending.case_number,
        )
        assert completed.reply_text

        db = SessionLocal()
        assert db.execute(text("select status from request_log where log_id=:id"), {"id": log_id}).scalar_one() == "success"
        # Smallest bucket (104) consumed first, fully covering the 15 needed --
        # 105-bucket must be untouched.
        rows = dict(db.execute(text(
            "select boxes_per_pallet,pallet_count from uchoice_storage where warehouse_code='JFK' and sku_code=:sku"
        ), {"sku": sku}).all())
        assert rows.get(105) == 4       # untouched
        assert rows.get(89) == 1        # 104 - 15 leftover bucket
        assert 104 not in rows or rows.get(104) == 0
    finally:
        try:
            db.rollback()
        except Exception:
            db = SessionLocal()
        if staff_id:
            sessions = db.execute(text("select session_id from conversation_session where opened_by_staff_id=:staff"), {"staff": staff_id}).scalars().all()
            db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {"staff": staff_id, "sessions": sessions})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from request_log where submitted_by_staff_id=:staff or log_id=:log"), {"staff": staff_id, "log": log_id})
            db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
        db.execute(text("delete from uchoice_storage_txn where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_storage where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_sku where sku_code=:sku"), {"sku": sku})
        db.commit()
        db.close()


def test_kefu_outbound_completion_insufficient_stock_shows_real_bucket_breakdown(monkeypatch):
    """
    The insufficient-stock rejection used to just say "库存不足以自动分配"
    with no indication of what's actually on hand -- same class of gap
    fixed for move_storage's _move_storage_stock_available. Now reports
    the real bucket breakdown, matching that treatment.
    """
    from models.group import GroupConfig
    from models.role import Role
    from models.service import ServiceType

    db = SessionLocal()
    staff_id = original_session_id = log_id = None
    sku = f"ki{uuid.uuid4().hex[:8]}"
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        role = db.query(Role).filter_by(name="admin").one()
        outbound_type = db.query(ServiceType).filter_by(name="uchoice_outbound_request").one()
        staff = KefuStaff(
            open_kfid=f"kf-short-{uuid.uuid4().hex[:8]}",
            external_userid=f"short-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=role.role_id,
        )
        db.add(staff)
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:sku,'Kefu insufficient-stock test')"), {"sku": sku})
        # Only 20 boxes total on hand (5/托x1 + 15/托x1), but 50 will be requested.
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) "
            "values ('JFK',:sku,5,1),('JFK',:sku,15,1)"
        ), {"sku": sku})
        db.flush()
        original = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="completed",
            conversation_history=[],
            collected_fields={
                "warehouse_code": "JFK",
                "sku_lines": [{"sku_code": sku, "box_count": 50}],
            },
            source_channel="kefu",
            opened_by_staff_id=staff.staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(original)
        db.flush()
        log = RequestLog(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=outbound_type.service_type_id,
            status="processing",
            raw_message="original outbound",
            source_channel="kefu",
            submitted_by_staff_id=staff.staff_id,
            origin_session_id=original.session_id,
        )
        db.add(log)
        db.flush()
        original.request_log_id = log.log_id
        db.commit()
        db.refresh(staff)
        db.refresh(original)
        db.refresh(log)
        staff_id, original_session_id, log_id = staff.staff_id, original.session_id, log.log_id
        identity = KefuIdentity(staff.open_kfid, staff.external_userid)
        serial = log.serial_number
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request", service_type_name="confirm_outbound_completion",
            extracted_fields={"reference_serial": serial}, all_fields_collected=True, reply="ready",
        ))
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        result = processor(
            identity=identity, message_content=f"confirm {serial}",
            message_meta={"msgid": f"short-open-{uuid.uuid4().hex}"}, case_number_hint=None,
        )
        assert "确认以下信息" not in result.reply_text  # never reaches confirmation
        assert "需要 50 箱" in result.reply_text
        assert "5/托（现有1托）" in result.reply_text
        assert "15/托（现有1托）" in result.reply_text
        assert "共 20 箱" in result.reply_text  # total on hand, plainly stated

        db = SessionLocal()
        # Untouched -- nothing was applied.
        rows = dict(db.execute(text(
            "select boxes_per_pallet,pallet_count from uchoice_storage where warehouse_code='JFK' and sku_code=:sku"
        ), {"sku": sku}).all())
        db.close()
        assert rows == {5: 1, 15: 1}
    finally:
        try:
            db.rollback()
        except Exception:
            db = SessionLocal()
        if staff_id:
            sessions = db.execute(text("select session_id from conversation_session where opened_by_staff_id=:staff"), {"staff": staff_id}).scalars().all()
            db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {"staff": staff_id, "sessions": sessions})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from request_log where submitted_by_staff_id=:staff or log_id=:log"), {"staff": staff_id, "log": log_id})
            db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
            db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": sessions})
            db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
        db.execute(text("delete from uchoice_storage_txn where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_storage where sku_code=:sku"), {"sku": sku})
        db.execute(text("delete from uchoice_sku where sku_code=:sku"), {"sku": sku})
        db.commit()
        db.close()
