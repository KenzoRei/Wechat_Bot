"""
Kefu outbound -> new address -> resume (address-pivot plan rev 3, items 2-3),
end to end through the case processor against real PostgreSQL. The AI is
mocked per turn.

Isolation: warehouse JFK (the pivot only carries real warehouse codes) with
a synthetic SKU, so no real inventory bucket is touched. Cleanup is by the
staff/SKU/address rows each test created.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from ai.base import AIResponse, AddressMatch
from core import parked_outbound
from core.kefu_contracts import CaseTurnDenied, CaseTurnSuccess, KefuIdentity
from database import SessionLocal
import core.kefu_case_adapter as adapter

WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"
WAREHOUSE = "JFK"
ADDR = "1 Main St, Jamaica, NY 11434"


class World:
    def __init__(self):
        self.staff: list = []
        self.sku = None

    def staff_member(self, role="admin", warehouse_codes=None):
        from models.group import GroupConfig
        from models.kefu import KefuStaff
        from models.role import Role
        db = SessionLocal()
        group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).one()
        staff = KefuStaff(
            open_kfid=f"kf-resume-{uuid.uuid4().hex[:8]}",
            external_userid=f"staff-resume-{uuid.uuid4().hex[:8]}",
            group_id=group.group_id,
            role_id=db.query(Role).filter_by(name=role).one().role_id,
            warehouse_codes=warehouse_codes,
        )
        db.add(staff)
        db.commit()
        result = (staff.staff_id, KefuIdentity(staff.open_kfid, staff.external_userid))
        db.close()
        self.staff.append(result[0])
        return result

    def stock(self, buckets: dict[int, int]):
        db = SessionLocal()
        self.sku = f"rs{uuid.uuid4().hex[:8]}"
        db.execute(text("insert into uchoice_sku(sku_code,description) values (:s,'Resume test SKU')"), {"s": self.sku})
        for bpp, count in buckets.items():
            db.execute(text(
                "insert into uchoice_storage(warehouse_code,sku_code,boxes_per_pallet,pallet_count) values (:w,:s,:b,:c)"
            ), {"w": WAREHOUSE, "s": self.sku, "b": bpp, "c": count})
        db.commit()
        db.close()

    def cleanup(self):
        db = SessionLocal()
        staff = [str(s) for s in self.staff]
        sessions = db.execute(text(
            "select session_id from conversation_session where opened_by_staff_id::text = any(:staff)"
        ), {"staff": staff}).scalars().all()
        db.execute(text("delete from case_turn where acting_staff_id::text = any(:staff) or session_id = any(:s)"),
                   {"staff": staff, "s": sessions})
        db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id::text = any(:staff)"), {"staff": staff})
        db.execute(text("delete from case_execution where session_id = any(:s)"), {"s": sessions})
        db.execute(text("delete from kefu_staff_case_context where staff_id::text = any(:staff)"), {"staff": staff})
        db.execute(text("update conversation_session set request_log_id = null where session_id = any(:s)"), {"s": sessions})
        db.execute(text("delete from request_log where submitted_by_staff_id::text = any(:staff)"), {"staff": staff})
        db.execute(text("delete from conversation_session where session_id = any(:s)"), {"s": sessions})
        db.execute(text("delete from uchoice_address where created_by = any(:staff)"), {"staff": staff})
        if self.sku:
            db.execute(text("delete from uchoice_storage_txn where sku_code = :s"), {"s": self.sku})
            db.execute(text("delete from uchoice_storage where sku_code = :s"), {"s": self.sku})
            db.execute(text("delete from uchoice_sku where sku_code = :s"), {"s": self.sku})
        db.execute(text("delete from kefu_staff where staff_id::text = any(:staff)"), {"staff": staff})
        db.commit()
        db.close()


@pytest.fixture
def world():
    w = World()
    yield w
    w.cleanup()


@pytest.fixture
def ai(monkeypatch):
    """Queue of AI responses, one per turn. Direct (non-case) sends are
    recorded instead of reaching a Kefu client."""
    queue: list = []
    monkeypatch.setattr(adapter._ai_chain, "process", lambda context: queue.pop(0))
    monkeypatch.setattr(adapter, "_direct_send", lambda client, identity, key, text_: None)
    return queue


def turn(identity, content, case_number=None):
    processor = adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)
    return processor(
        identity=identity, message_content=content,
        message_meta={"msgid": f"resume-{uuid.uuid4().hex}"}, case_number_hint=case_number,
    )


PARTS = {"street": "1 Main St", "city": "Jamaica", "state": "NY", "zip": "11434"}


def outbound_turn(world, *, minutes=12, charge_type=None, intent="continuation", parts=PARTS):
    # The AI's own addr text is messy on purpose: the stored and displayed
    # address must be the one code builds from the parts (ADDR).
    new_address = {"company_name": "ABC Corp", "addr": "1 main st jamaica ny11434"}
    if parts is not None:
        new_address["addr_parts"] = parts
    if minutes is not None:
        new_address["estimated_drive_minutes"] = minutes
    if charge_type:
        new_address["charge_type"] = charge_type
    return AIResponse(
        intent=intent, service_type_name="uchoice_outbound_request", reply="",
        extracted_fields={}, all_fields_collected=False,
        address_match=AddressMatch(status="unmatched", candidate_ids=(), new_address=new_address),
    )


def draft_turn(world):
    """The outbound's first message: goods, no destination yet."""
    return AIResponse(
        intent="new_request", service_type_name="uchoice_outbound_request", reply="",
        extracted_fields={"warehouse_code": WAREHOUSE,
                          "sku_lines": [{"sku_code": world.sku, "boxes_per_pallet": 10, "pallet_count": 1}]},
        all_fields_collected=False,
        address_match=AddressMatch(status="not_provided", candidate_ids=()),
    )


def simple(intent):
    return AIResponse(intent=intent, service_type_name=None, reply="", extracted_fields={}, all_fields_collected=False)


def cases(staff_id):
    """Both cases of the handoff, by service name."""
    db = SessionLocal()
    rows = db.execute(text(
        "select st.name, cs.session_id, cs.status, cs.collected_fields, cs.case_number, cs.expires_at, "
        "rl.status as log_status, rl.serial_number "
        "from conversation_session cs join service_type st on st.service_type_id = cs.service_type_id "
        "left join request_log rl on rl.log_id = cs.request_log_id "
        "where cs.opened_by_staff_id = :staff order by cs.created_at"
    ), {"staff": staff_id}).mappings().all()
    db.close()
    return {row["name"]: row for row in rows}


def binding(staff_id):
    db = SessionLocal()
    value = db.execute(text("select active_session_id from kefu_staff_case_context where staff_id = :s"),
                       {"s": staff_id}).scalar()
    db.close()
    return value


def start(world, ai, **kwargs):
    """Two messages: the outbound draft (so it has its own case number),
    then the unsaved destination that hands off to a new address."""
    staff_id, identity = world.staff_member()
    world.stock({10: 3})
    ai.append(draft_turn(world))
    turn(identity, "JFK 发 1 托")
    ai.append(outbound_turn(world, **kwargs))
    result = turn(identity, "送到 ABC Corp, 1 Main St, Jamaica, NY 11434")
    return staff_id, identity, result


# ── 1, 2, 11: handoff, confirm, resume, submit ───────────────────────────────

def test_handoff_parks_outbound_and_confirm_resumes_it(world, ai):
    staff_id, identity, result = start(world, ai)

    # 1: address case straight to confirmation, with the suggested tier
    assert isinstance(result, CaseTurnSuccess)
    assert "已暂存" in result.reply_text
    assert "配送（$45） — 预计车程约 12 分钟（JFK 仓出发，系统估算" in result.reply_text
    c = cases(staff_id)
    outbound, address = c["uchoice_outbound_request"], c["upsert_address"]
    assert (address["status"], address["log_status"]) == ("pending_confirmation", "pending")
    assert (outbound["status"], outbound["log_status"]) == ("active", "pending")
    assert outbound["collected_fields"][parked_outbound.PARKED_KEY] == str(address["session_id"])
    assert outbound["serial_number"] in result.reply_text
    assert outbound["expires_at"] == address["expires_at"]

    # 2: confirm the address -> saved, outbound resumed to its confirmation
    ai.append(simple("confirm"))
    result = turn(identity, "确认")
    assert "地址已新增：ABC Corp（1 Main St, Jamaica, NY 11434）" in result.reply_text
    assert f"继续出库申请 {outbound['serial_number']}" in result.reply_text
    c = cases(staff_id)
    outbound2, address2 = c["uchoice_outbound_request"], c["upsert_address"]
    assert address2["status"] == "completed"
    assert outbound2["status"] == "pending_confirmation"
    assert parked_outbound.PARKED_KEY not in outbound2["collected_fields"]
    db = SessionLocal()
    saved = db.execute(text("select address_id, charge_type, warehouse_code from uchoice_address "
                            "where created_by = :s"), {"s": str(staff_id)}).mappings().one()
    # 11: this turn is recorded on the address case; staff moves to the outbound
    turn_row = db.execute(text(
        "select session_id, customer_copy_text from case_turn where acting_staff_id = :s "
        "and role = 'user' order by created_at desc limit 1"
    ), {"s": staff_id}).mappings().one()
    history = db.execute(text("select conversation_history from conversation_session where session_id = :s"),
                         {"s": outbound2["session_id"]}).scalar()
    db.close()
    assert (saved["charge_type"], saved["warehouse_code"]) == ("delivery", WAREHOUSE)
    assert outbound2["collected_fields"]["destination_address_id"] == str(saved["address_id"])
    assert turn_row["session_id"] == address2["session_id"]
    assert "地址：1 Main St" in (turn_row["customer_copy_text"] or "")
    assert "商品明细" not in (turn_row["customer_copy_text"] or "")
    assert binding(staff_id) == outbound2["session_id"]
    assert history[-1]["role"] == "assistant" and "请确认" in history[-1]["content"]

    # submit the outbound as usual
    ai.append(simple("confirm"))
    turn(identity, "确认")
    assert cases(staff_id)["uchoice_outbound_request"]["log_status"] == "processing"


# ── 3: cancel during the address step cancels both (D1) ──────────────────────

def test_cancel_address_step_cancels_both(world, ai):
    staff_id, identity, _ = start(world, ai)
    ai.append(simple("cancel"))
    result = turn(identity, "取消")
    c = cases(staff_id)
    assert c["upsert_address"]["status"] == "cancelled"
    assert (c["uchoice_outbound_request"]["status"], c["uchoice_outbound_request"]["log_status"]) == ("cancelled", "cancelled")
    assert f"出库申请 {c['uchoice_outbound_request']['serial_number']} 也已一并取消" in result.reply_text


# ── 4: address saved under another warehouse ─────────────────────────────────

def test_address_warehouse_change_saves_address_and_cancels_outbound(world, ai):
    staff_id, identity, _ = start(world, ai)
    # The warehouse changes with no new estimate: the suggested charge type
    # no longer applies, so it is asked for and the case leaves confirmation.
    ai.append(AIResponse(intent="continuation", service_type_name=None, reply="",
                         extracted_fields={"warehouse_code": "DE"}, all_fields_collected=False))
    result = turn(identity, "所属仓库改成 DE")
    assert "计费类型" in result.reply_text
    assert cases(staff_id)["upsert_address"]["status"] == "active"
    ai.append(AIResponse(intent="continuation", service_type_name=None, reply="",
                         extracted_fields={}, all_fields_collected=False, estimated_drive_minutes=150))
    result = turn(identity, "继续")
    assert "卡车转仓（$85） — 预计车程约 150 分钟（DE 仓出发" in result.reply_text
    ai.append(simple("confirm"))
    result = turn(identity, "确认")
    c = cases(staff_id)
    assert c["upsert_address"]["status"] == "completed"
    assert c["uchoice_outbound_request"]["status"] == "cancelled"
    assert "所属仓库（DE）" in result.reply_text and "（JFK）不同" in result.reply_text


# ── 5: outbound closed before the address confirm ────────────────────────────

def test_outbound_closed_before_confirm_gets_expired_reply(world, ai):
    staff_id, identity, _ = start(world, ai)
    db = SessionLocal()
    db.execute(text("update conversation_session set status = 'cancelled' where session_id = :s"),
               {"s": cases(staff_id)["uchoice_outbound_request"]["session_id"]})
    db.commit()
    db.close()
    ai.append(simple("confirm"))
    result = turn(identity, "确认")
    assert cases(staff_id)["upsert_address"]["status"] == "completed"
    assert "已失效，请重新提交" in result.reply_text


# ── 6: stock dropped before the resume ───────────────────────────────────────

def test_stock_drop_before_resume_rejects_outbound_and_keeps_address(world, ai):
    staff_id, identity, _ = start(world, ai)
    db = SessionLocal()
    db.execute(text("update uchoice_storage set pallet_count = 0 where sku_code = :s"), {"s": world.sku})
    db.commit()
    db.close()
    ai.append(simple("confirm"))
    result = turn(identity, "确认")
    c = cases(staff_id)
    assert c["upsert_address"]["status"] == "completed"
    assert c["uchoice_outbound_request"]["status"] == "cancelled"
    assert "地址已新增" in result.reply_text and "现有 0 箱" in result.reply_text


# ── 7: no estimate -> charge type is asked ───────────────────────────────────

def test_no_estimate_asks_for_charge_type(world, ai):
    staff_id, _, result = start(world, ai, minutes=None)
    assert "计费类型" in result.reply_text and "系统估算" not in result.reply_text
    assert cases(staff_id)["upsert_address"]["status"] == "active"


# ── 8: expiry times out both ─────────────────────────────────────────────────

def test_expiry_times_out_the_parked_outbound_with_its_address_case(world, ai):
    from jobs import session_expiry
    from models.session import ConversationSession
    staff_id, _, _ = start(world, ai)
    c = cases(staff_id)
    db = SessionLocal()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.execute(text("update conversation_session set expires_at = :t where session_id = :s"),
               {"t": past, "s": c["uchoice_outbound_request"]["session_id"]})
    db.commit()
    session_expiry.run_expiry_check(db)   # address case not expired: outbound waits
    assert cases(staff_id)["uchoice_outbound_request"]["status"] == "active"
    session_expiry._expire_session(db, db.get(ConversationSession, c["upsert_address"]["session_id"]))
    db.close()
    c = cases(staff_id)
    assert c["upsert_address"]["status"] == "timed_out"
    assert (c["uchoice_outbound_request"]["status"], c["uchoice_outbound_request"]["log_status"]) == ("timed_out", "timed_out")


# ── 9: stated charge type beats a simultaneous estimate ──────────────────────

def test_stated_charge_type_beats_the_estimate(world, ai):
    staff_id, _, result = start(world, ai, minutes=30, charge_type="self_pickup")
    fields = cases(staff_id)["upsert_address"]["collected_fields"]
    assert fields["charge_type"] == "self_pickup"
    assert "_charge_type_suggested" not in fields and "系统估算" not in result.reply_text


# ── 10: the estimate survives sanitizing (fails if the sanitizer strips it) ──

def test_handoff_estimate_survives_sanitizing(world, ai):
    staff_id, _, _ = start(world, ai, minutes=25)
    fields = cases(staff_id)["upsert_address"]["collected_fields"]
    assert (fields["charge_type"], fields["_charge_type_estimate_minutes"]) == ("truck_transfer", 25)


# ── 12-14: turns sent to the parked outbound case ────────────────────────────

def test_continuation_by_outbound_case_number_is_redirected(world, ai):
    staff_id, identity, _ = start(world, ai)
    before = cases(staff_id)
    ai.append(AIResponse(intent="continuation", service_type_name=None, reply="",
                         extracted_fields={"note": "后门卸货"}, all_fields_collected=False))
    result = turn(identity, "备注：后门卸货", case_number=before["uchoice_outbound_request"]["case_number"])
    after = cases(staff_id)
    assert result.reply_text.startswith(
        f"出库申请 {before['uchoice_outbound_request']['serial_number']} 正在等待新增地址，以下为地址申请：")
    assert after["upsert_address"]["collected_fields"].get("note") == "后门卸货"
    assert after["uchoice_outbound_request"]["collected_fields"] == before["uchoice_outbound_request"]["collected_fields"]


def test_cancel_by_outbound_case_number_cancels_both(world, ai):
    staff_id, identity, _ = start(world, ai)
    ai.append(simple("cancel"))
    turn(identity, "取消", case_number=cases(staff_id)["uchoice_outbound_request"]["case_number"])
    c = cases(staff_id)
    assert c["upsert_address"]["status"] == c["uchoice_outbound_request"]["status"] == "cancelled"


def test_second_staff_member_is_redirected_or_denied(world, ai):
    staff_id, _, _ = start(world, ai)
    outbound_case = cases(staff_id)["uchoice_outbound_request"]["case_number"]

    _, other_admin = world.staff_member("admin")
    ai.append(AIResponse(intent="continuation", service_type_name=None, reply="",
                         extracted_fields={}, all_fields_collected=False))
    result = turn(other_admin, "进度？", case_number=outbound_case)
    assert "正在等待新增地址" in result.reply_text

    _, de_only = world.staff_member("warehouseman", warehouse_codes=["DE"])
    result = turn(de_only, "进度？", case_number=outbound_case)
    assert isinstance(result, CaseTurnDenied)


# ── 15: orphaned parked outbound ─────────────────────────────────────────────

def test_orphaned_parked_outbound_is_closed_on_its_next_turn(world, ai):
    staff_id, identity, _ = start(world, ai)
    c = cases(staff_id)
    db = SessionLocal()
    db.execute(text("update conversation_session set status = 'cancelled' where session_id = :s"),
               {"s": c["upsert_address"]["session_id"]})
    db.commit()
    db.close()
    result = turn(identity, "继续", case_number=c["uchoice_outbound_request"]["case_number"])
    assert result.reply_text == f"出库申请 {c['uchoice_outbound_request']['serial_number']} 已失效，请重新提交。"
    assert cases(staff_id)["uchoice_outbound_request"]["status"] == "cancelled"


# ── Audit fixes ──────────────────────────────────────────────────────────────

def test_user_stating_the_suggested_tier_keeps_it_as_stated(world, ai):
    """Audit #2: the same tier as the suggestion, said by the user, sticks."""
    staff_id, identity, _ = start(world, ai)
    ai.append(AIResponse(intent="continuation", service_type_name=None, reply="",
                         extracted_fields={"charge_type": "delivery"}, all_fields_collected=False,
                         charge_type_stated=True))
    result = turn(identity, "就按配送")
    fields = cases(staff_id)["upsert_address"]["collected_fields"]
    assert fields["charge_type"] == "delivery" and "_charge_type_suggested" not in fields
    assert "系统估算" not in result.reply_text


def test_redirect_locks_address_case_before_outbound(monkeypatch):
    """Audit #3: one lock order (address -> outbound) everywhere."""
    from types import SimpleNamespace
    order = []
    address = SimpleNamespace(session_id="A", status="active", collected_fields={}, group_id="g")
    outbound = SimpleNamespace(session_id="O", status="active",
                               collected_fields={parked_outbound.PARKED_KEY: "A"}, request_log_id=None)
    monkeypatch.setattr(parked_outbound, "lock_session",
                        lambda db, sid: order.append(str(sid)) or {"A": address, "O": outbound}[str(sid)])
    monkeypatch.setattr(adapter, "_authorize_case", lambda access, session: None)
    kind, value, _ = adapter._redirect_parked_outbound(None, None, outbound, explicit=True)
    assert (kind, value, order) == ("session", address, ["A", "O"])


def test_address_without_valid_parts_gets_no_suggestion(world, ai):
    """No ZIP in the AI's reading: the address isn't verified, so no tier is
    suggested and the AI's own address text is kept."""
    staff_id, _, result = start(world, ai, parts={k: v for k, v in PARTS.items() if k != "zip"})
    fields = cases(staff_id)["upsert_address"]["collected_fields"]
    assert fields["addr"] == "1 main st jamaica ny11434"
    assert "charge_type" not in fields and "_addr_verified" not in fields
    assert "计费类型" in result.reply_text and "系统估算" not in result.reply_text


def test_valid_parts_replace_the_messy_address(world, ai):
    staff_id, _, result = start(world, ai)
    fields = cases(staff_id)["upsert_address"]["collected_fields"]
    assert fields["addr"] == ADDR and fields["_addr_verified"] == ADDR
    assert ADDR in result.reply_text and "1 main st jamaica" not in result.reply_text
