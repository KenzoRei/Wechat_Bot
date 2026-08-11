"""Real-Postgres coverage for the Kefu-native confirmation claim."""
import threading
import uuid

from sqlalchemy import text

from ai.base import AIResponse
from core.kefu_contracts import CaseTurnSuccess, KefuIdentity
from database import SessionLocal
import core.kefu_case_adapter as adapter


WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _seed():
    from models.group import GroupConfig
    from models.kefu import KefuStaff, UchoiceCustomer
    from models.role import Role

    db = SessionLocal()
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).one()
    role = db.query(Role).filter_by(name="admin").one()
    staff = KefuStaff(
        open_kfid=f"kf-confirm-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-confirm-{uuid.uuid4().hex[:8]}",
        group_id=group.group_id,
        role_id=role.role_id,
    )
    customer = UchoiceCustomer(
        customer_code=f"CONF-{uuid.uuid4().hex[:8]}",
        canonical_name="Confirmation Test Customer",
    )
    db.add_all([staff, customer])
    db.commit()
    db.refresh(staff)
    db.refresh(customer)
    values = (staff.staff_id, staff.open_kfid, staff.external_userid, customer.customer_id)
    db.close()
    return values


def _cleanup(staff_id, customer_id, address_id=None, storage_key=None):
    db = SessionLocal()
    session_ids = db.execute(text(
        "select session_id from conversation_session where opened_by_staff_id=:staff"
    ), {"staff": staff_id}).scalars().all()
    db.execute(text("delete from case_turn where acting_staff_id=:staff or session_id=any(:sessions)"), {
        "staff": staff_id, "sessions": session_ids,
    })
    db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from case_execution where session_id=any(:sessions)"), {"sessions": session_ids})
    db.execute(text("delete from request_log where submitted_by_staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from kefu_staff_case_context where staff_id=:staff"), {"staff": staff_id})
    db.execute(text("delete from conversation_session where session_id=any(:sessions)"), {"sessions": session_ids})
    db.execute(text("delete from kefu_staff where staff_id=:staff"), {"staff": staff_id})
    if address_id is not None:
        db.execute(text("delete from uchoice_address where address_id=:address"), {"address": address_id})
    if storage_key is not None:
        db.execute(text(
            "delete from uchoice_storage where warehouse_code=:warehouse and sku_code=:sku and boxes_per_pallet=:bpp"
        ), {"warehouse": storage_key[0], "sku": storage_key[1], "bpp": storage_key[2]})
    db.execute(text("delete from uchoice_customer where customer_id=:customer"), {"customer": customer_id})
    db.commit()
    db.close()


def test_two_simultaneous_confirmations_execute_business_once(monkeypatch):
    staff_id, open_kfid, external_userid, customer_id = _seed()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)

    try:
        db = SessionLocal()
        sku_code = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        db.close()

        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: AIResponse(
            intent="new_request",
            reply="ready",
            extracted_fields={
                "customer_id": str(customer_id),
                "sku_lines": [{"sku_code": sku_code, "boxes_per_pallet": 48, "pallet_count": 1}],
                "needs_unpacking": False,
            },
            all_fields_collected=True,
            service_type_name="uchoice_inbound_request",
        ))
        first = processor(
            identity=identity,
            message_content="create inbound",
            message_meta={"msgid": f"confirm-create-{uuid.uuid4().hex[:8]}"},
            case_number_hint=None,
        )
        assert isinstance(first, CaseTurnSuccess)
        assert "请确认以下信息" in first.reply_text

        barrier = threading.Barrier(2)

        def confirm_response(context):
            barrier.wait(timeout=10)
            return AIResponse(
                intent="confirm", reply="确认", extracted_fields={},
                all_fields_collected=True, service_type_name=None,
            )

        monkeypatch.setattr(adapter._ai_chain, "process", confirm_response)
        results, errors = [], []

        def run_confirm(label):
            try:
                results.append(processor(
                    identity=identity,
                    message_content="确认",
                    message_meta={"msgid": f"confirm-race-{label}-{uuid.uuid4().hex[:8]}"},
                    case_number_hint=first.case_number,
                ))
            except Exception as exc:  # pragma: no cover - included in assertion diagnostics
                errors.append(exc)

        threads = [threading.Thread(target=run_confirm, args=(label,)) for label in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert not errors
        assert len(results) == 2
        assert all(isinstance(result, CaseTurnSuccess) for result in results)
        assert sum("不能重复确认" not in result.reply_text for result in results) == 1

        db = SessionLocal()
        session = db.execute(text(
            "select session_id, status, request_log_id from conversation_session where case_number=:case"
        ), {"case": first.case_number}).mappings().one()
        log_status = db.execute(text(
            "select status from request_log where log_id=:log"
        ), {"log": session["request_log_id"]}).scalar_one()
        logical_rows = db.execute(text(
            "select status from case_execution where execution_key like :key"
        ), {"key": f"kefu-confirm:{session['session_id']}:%"}).scalars().all()
        db.close()
        assert session["status"] == "completed"
        assert log_status == "processing"  # awaits warehouse completion
        assert logical_rows == ["completed"]
    finally:
        _cleanup(staff_id, customer_id)


def test_outbound_confirmation_enqueues_regenerable_pdf_and_safe_copy(monkeypatch):
    from core.kefu_artifact_loader import load_artifact
    from core.kefu_delivery import content_hash
    from models.uchoice import UchoiceAddress

    staff_id, open_kfid, external_userid, customer_id = _seed()
    identity = KefuIdentity(open_kfid, external_userid)
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    address_id = None
    storage_key = None
    try:
        db = SessionLocal()
        sku_code = db.execute(text("select sku_code from uchoice_sku order by sku_code limit 1")).scalar_one()
        bpp = 900000 + int(uuid.uuid4().hex[:4], 16)
        db.execute(text(
            "insert into uchoice_storage(warehouse_code, sku_code, boxes_per_pallet, pallet_count) "
            "values ('JFK', :sku, :bpp, 5)"
        ), {"sku": sku_code, "bpp": bpp})
        storage_key = ("JFK", sku_code, bpp)
        address = UchoiceAddress(
            company_name="Copy Safe Customer",
            charge_type="delivery",
            addr="100 Customer Way",
            warehouse_code="JFK",
            note="INTERNAL SECRET NOTE",
            created_by="integration-test",
            customer_id=customer_id,
        )
        db.add(address)
        db.commit()
        db.refresh(address)
        address_id = address.address_id
        db.close()

        responses = iter([
            AIResponse(
                intent="new_request", reply="ready",
                extracted_fields={
                    "customer_id": str(customer_id),
                    "destination_address_id": str(address_id),
                    "sku_lines": [{
                        "sku_code": sku_code,
                        "boxes_per_pallet": bpp,
                        "pallet_count": 1,
                    }],
                },
                all_fields_collected=True,
                service_type_name="uchoice_outbound_request",
            ),
            AIResponse(
                intent="confirm", reply="确认", extracted_fields={},
                all_fields_collected=True, service_type_name=None,
            ),
        ])
        monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(responses))
        pending = processor(
            identity=identity,
            message_content="create outbound",
            message_meta={"msgid": f"outbound-create-{uuid.uuid4().hex[:8]}"},
            case_number_hint=None,
        )
        confirm_msgid = f"outbound-confirm-{uuid.uuid4().hex[:8]}"
        result = processor(
            identity=identity,
            message_content="确认",
            message_meta={"msgid": confirm_msgid},
            case_number_hint=pending.case_number,
        )

        assert isinstance(result, CaseTurnSuccess)
        assert result.customer_copy_text
        assert "Copy Safe Customer，100 Customer Way" in result.customer_copy_text
        assert "INTERNAL SECRET" not in result.customer_copy_text
        assert "charge_type" not in result.customer_copy_text
        assert len(result.artifacts) == 1
        produced = result.artifacts[0]

        db = SessionLocal()
        delivery = db.execute(text(
            "select artifact_request_log_id, artifact_doc_type, artifact_key, payload_hash "
            "from kefu_outbound_delivery where recipient_staff_id=:staff and payload_type='file'"
        ), {"staff": staff_id}).mappings().one()
        db.close()
        rebuilt = load_artifact(
            delivery["artifact_request_log_id"],
            delivery["artifact_doc_type"],
            delivery["artifact_key"],
        )
        assert rebuilt.artifact_key == produced["artifact_key"]
        assert content_hash(rebuilt.content) == delivery["payload_hash"]

        replay = processor(
            identity=identity,
            message_content="确认",
            message_meta={"msgid": confirm_msgid},
            case_number_hint=pending.case_number,
        )
        assert replay.reply_text == result.reply_text
        assert replay.customer_copy_text == result.customer_copy_text
        assert len(replay.artifacts) == 1
        assert replay.artifacts[0].artifact_key == produced["artifact_key"]
    finally:
        _cleanup(staff_id, customer_id, address_id, storage_key)
