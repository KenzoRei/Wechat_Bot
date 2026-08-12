import uuid

from sqlalchemy import text

from ai.base import AIResponse, AddressMatch
from core.kefu_contracts import CaseTurnSuccess, KefuIdentity
from database import SessionLocal
import core.kefu_case_adapter as adapter


WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _seed_staff():
    from models.group import GroupConfig
    from models.kefu import KefuStaff
    from models.role import Role
    db = SessionLocal()
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).one()
    role = db.query(Role).filter_by(name="admin").one()
    staff = KefuStaff(
        open_kfid=f"kf-pivot-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-pivot-{uuid.uuid4().hex[:8]}",
        group_id=group.group_id,
        role_id=role.role_id,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    result = staff.staff_id, staff.open_kfid, staff.external_userid
    db.close()
    return result


def _cleanup(staff_id, warehouse, sku):
    db = SessionLocal()
    sessions = db.execute(text(
        "select session_id from conversation_session where opened_by_staff_id=:staff"
    ), {"staff": staff_id}).scalars().all()
    db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {"staff": staff_id, "sessions": sessions})
    db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": sessions})
    db.execute(text("delete from request_log where submitted_by_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": sessions})
    db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from uchoice_storage_txn where warehouse_code=:wh and sku_code=:sku"), {"wh": warehouse, "sku": sku})
    db.execute(text("delete from uchoice_storage where warehouse_code=:wh and sku_code=:sku"), {"wh": warehouse, "sku": sku})
    db.commit()
    db.close()


def test_unmatched_address_atomically_pivots_without_ai_operational_prose(monkeypatch):
    staff_id, open_kfid, external_userid = _seed_staff()
    warehouse, sku = "JFK", "s2"
    try:
        db = SessionLocal()
        db.execute(text("delete from uchoice_storage where warehouse_code=:wh and sku_code=:sku"), {"wh": warehouse, "sku": sku})
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:wh,:sku,80,2)"
        ), {"wh": warehouse, "sku": sku})
        db.commit()
        db.close()

        marker = "AI MUST NEVER SEND THIS"
        calls = {"count": 0}
        def ai_response(context):
            calls["count"] += 1
            return AIResponse(
                intent="new_request",
                service_type_name="uchoice_outbound_request",
                extracted_fields={
                    "sku_lines": [{"sku_code": sku, "boxes_per_pallet": 72, "pallet_count": 2}],
                },
                address_match=AddressMatch(
                    status="unmatched",
                    candidate_ids=(),
                    new_address={"company_name": "YANWEN EXPRESS LLC", "addr": "600 Blair Rd, Carteret, NJ 07008"},
                ),
                reply=marker,
                all_fields_collected=False,
            )
        monkeypatch.setattr(adapter._ai_chain, "process", ai_response)
        processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
        msgid = f"pivot-{uuid.uuid4().hex}"
        result = processor(
            identity=KefuIdentity(open_kfid, external_userid),
            message_content="send two pallets to a new address",
            message_meta={"msgid": msgid},
            case_number_hint=None,
        )
        replay = processor(
            identity=KefuIdentity(open_kfid, external_userid),
            message_content="send two pallets to a new address",
            message_meta={"msgid": msgid},
            case_number_hint=None,
        )

        assert isinstance(result, CaseTurnSuccess)
        assert replay.reply_text == result.reply_text
        assert replay.case_number == result.case_number
        assert calls["count"] == 1
        assert marker not in result.reply_text
        assert "新增地址流程" in result.reply_text

        db = SessionLocal()
        rows = db.execute(text(
            "select st.name, cs.status as session_status, cs.collected_fields, rl.status as log_status "
            "from conversation_session cs join service_type st on st.service_type_id=cs.service_type_id "
            "left join request_log rl on rl.log_id=cs.request_log_id "
            "where cs.opened_by_staff_id=:staff order by cs.created_at"
        ), {"staff": staff_id}).mappings().all()
        db.close()
        by_service = {row["name"]: row for row in rows}
        assert set(by_service) == {"uchoice_outbound_request", "upsert_address"}
        assert (by_service["uchoice_outbound_request"]["session_status"],
                by_service["uchoice_outbound_request"]["log_status"]) == ("cancelled", "cancelled")
        assert (by_service["upsert_address"]["session_status"],
                by_service["upsert_address"]["log_status"]) == ("active", "pending")
        assert by_service["upsert_address"]["collected_fields"]["addr"] == "600 Blair Rd, Carteret, NJ 07008"
    finally:
        _cleanup(staff_id, warehouse, sku)


def test_insufficient_boxes_reject_before_unmatched_address_pivot(monkeypatch):
    staff_id, open_kfid, external_userid = _seed_staff()
    warehouse, sku = "JFK", "s2"
    try:
        db = SessionLocal()
        db.execute(text("delete from uchoice_storage where warehouse_code=:wh and sku_code=:sku"), {"wh": warehouse, "sku": sku})
        db.execute(text(
            "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:wh,:sku,143,1)"
        ), {"wh": warehouse, "sku": sku})
        db.commit()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request",
            service_type_name="uchoice_outbound_request",
            extracted_fields={"sku_lines": [{"sku_code": sku, "boxes_per_pallet": 72, "pallet_count": 2}]},
            address_match=AddressMatch(
                status="unmatched", candidate_ids=(),
                new_address={"company_name": "YANWEN EXPRESS LLC", "addr": "600 Blair Rd, Carteret, NJ 07008"},
            ),
            reply="FALSE PIVOT CLAIM",
            all_fields_collected=False,
        ))
        result = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)(
            identity=KefuIdentity(open_kfid, external_userid),
            message_content="send two pallets to a new address",
            message_meta={"msgid": f"short-{uuid.uuid4().hex}"},
            case_number_hint=None,
        )

        assert "申请 144 箱" in result.reply_text
        assert "现有 143 箱" in result.reply_text
        assert "新增地址流程" not in result.reply_text
        assert "FALSE PIVOT CLAIM" not in result.reply_text

        db = SessionLocal()
        names = db.execute(text(
            "select st.name from conversation_session cs join service_type st on st.service_type_id=cs.service_type_id "
            "where cs.opened_by_staff_id=:staff"
        ), {"staff": staff_id}).scalars().all()
        db.close()
        assert names == ["uchoice_outbound_request"]
    finally:
        _cleanup(staff_id, warehouse, sku)
