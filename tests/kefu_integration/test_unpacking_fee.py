"""
柜号 / 拆柜费 end to end through the Kefu case processor, real PostgreSQL
(V41 applied). Plan: docs/ai-collaboration/2026-10-unpacking-fee/plan.md
rev 5 -- test numbers refer to its Tests section. The AI is scripted per
turn; deterministic turns (是/否, 确认 at a batch, numbers) call nothing.
"""
import io
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from ai.base import AIResponse
from core.kefu_contracts import KefuIdentity
from database import SessionLocal
from tests.kefu_integration.test_completion_batch import World, pallets


@pytest.fixture
def world():
    w = World()
    yield w
    w.cleanup()


def _staff(world, role, warehouse_codes=None):
    from models.kefu import KefuStaff
    from models.role import Role
    db = SessionLocal()
    try:
        staff = KefuStaff(
            open_kfid=f"kf-uf-{world.tag}", external_userid=f"uf-{uuid.uuid4().hex[:8]}",
            group_id=world.group_id(db), role_id=db.query(Role).filter_by(name=role).one().role_id,
            warehouse_codes=warehouse_codes,
        )
        db.add(staff)
        db.commit()
        world.staff_ids.append(staff.staff_id)
        return staff.staff_id, KefuIdentity(staff.open_kfid, staff.external_userid)
    finally:
        db.close()


def _processor(monkeypatch, script):
    import core.kefu_case_adapter as adapter
    it = iter(script)
    monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(it))
    monkeypatch.setattr(adapter, "_direct_send", lambda *a, **k: None)
    return adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)


def _turn(processor, identity, content, case=None):
    return processor(identity=identity, message_content=content,
                     message_meta={"msgid": f"uf-{uuid.uuid4().hex}"}, case_number_hint=case)


def _ai(intent="continuation", service=None, **fields):
    return AIResponse(intent=intent, service_type_name=service, extracted_fields=fields,
                      all_fields_collected=False, reply="")


def _case(staff_id):
    db = SessionLocal()
    try:
        row = db.execute(text(
            "select cs.status, cs.collected_fields, rl.status as log_status from conversation_session cs "
            "left join request_log rl on rl.log_id = cs.request_log_id "
            "where cs.opened_by_staff_id = :s order by cs.created_at desc limit 1"
        ), {"s": staff_id}).mappings().one()
        return dict(row)
    finally:
        db.close()


def _result(log_id):
    db = SessionLocal()
    try:
        return db.execute(text("select status, result from request_log where log_id=:id"), {"id": log_id}).mappings().one()
    finally:
        db.close()


# ── Requests (1, 2, 3, 13a, 14, 16, 19a) ─────────────────────────────────────

@pytest.fixture
def requester(world):
    db = SessionLocal()
    sku = world.sku(db)
    db.commit()
    db.close()
    staff_id, me = _staff(world, "customer")
    return staff_id, me, sku


def _new_inbound(sku, **extra):
    return _ai("new_request", "uchoice_inbound_request",
               sku_lines=[{"sku_code": sku, "boxes_per_pallet": 10, "pallet_count": 1}], **extra)


def test_request_asks_container_and_shows_it(requester, monkeypatch):
    """1 + 16: 柜号 asked; a standard value goes straight to the confirmation,
    which shows it; no warehouse stated still defaults to JFK."""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku), _ai(container_number="MSCU1234567")])
    asked = _turn(p, me, "入库 1托")
    assert "柜号是多少？（没有柜号请回复「无」）" in asked.reply_text
    shown = _turn(p, me, "MSCU1234567", asked.case_number)
    assert "柜号：MSCU1234567" in shown.reply_text and "JFK 仓" in shown.reply_text
    assert _case(staff_id)["status"] == "pending_confirmation"


def test_non_standard_container_is_double_checked_and_yes_never_submits(requester, monkeypatch):
    """2 + 13a + 19a: XYZ123 answering the 柜号 question -> double-check; 是 is
    answered in code: value accepted, confirmation shown, nothing submitted."""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku), _ai(container_number="XYZ123")])
    asked = _turn(p, me, "入库 1托")
    check = _turn(p, me, "XYZ123", asked.case_number)
    assert "柜号「XYZ123」不是标准格式" in check.reply_text
    shown = _turn(p, me, "是", check.case_number)                      # no AI call
    case = _case(staff_id)
    assert "柜号：XYZ123" in shown.reply_text
    assert (case["status"], case["log_status"]) == ("pending_confirmation", "pending")


def test_double_check_replaced_by_a_new_value(requester, monkeypatch):
    """2: a different value after the question is checked afresh."""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku), _ai(container_number="XYZ123"),
                                 _ai(container_number="MSCU1234567")])
    asked = _turn(p, me, "入库 1托")
    _turn(p, me, "XYZ123", asked.case_number)
    shown = _turn(p, me, "MSCU1234567", asked.case_number)
    assert "柜号：MSCU1234567" in shown.reply_text


def test_no_to_a_double_check_asks_again(requester, monkeypatch):
    """14"""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku), _ai(container_number="XYZ123")])
    asked = _turn(p, me, "入库 1托")
    _turn(p, me, "XYZ123", asked.case_number)
    again = _turn(p, me, "否", asked.case_number)                       # no AI call
    fields = _case(staff_id)["collected_fields"]
    assert again.reply_text == "柜号是多少？（没有柜号请回复「无」）"
    assert "container_number" not in fields and "_pending_value_check" not in fields


def test_request_with_no_container(requester, monkeypatch):
    """3"""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku, container_number="无")])
    shown = _turn(p, me, "入库 1托，没有柜号")
    assert "柜号：无" in shown.reply_text


def test_ai_corrected_container_is_ignored(requester, monkeypatch):
    """17: the AI 'fixes' MSCU123456 -> the typed value is double-checked."""
    staff_id, me, sku = requester
    p = _processor(monkeypatch, [_new_inbound(sku), _ai(container_number="MSCU1234567")])
    asked = _turn(p, me, "入库 1托")
    check = _turn(p, me, "MSCU123456", asked.case_number)
    assert "柜号「MSCU123456」不是标准格式" in check.reply_text


# ── Receipts (4-10, 13b, 15, 17, 18, 19b) ────────────────────────────────────

@pytest.fixture
def receipt(world):
    """One processing inbound request in a synthetic warehouse and a
    warehouseman for it. container=None leaves no 柜号 (a pre-V41 request)."""
    def make(container=None, legacy_unpacking=False):
        db = SessionLocal()
        try:
            w, sku = world.warehouse(), world.sku(db)
            log = world.request(db, w, pallets(10, 1, sku), direction="inbound")
            extra = {}
            if container is not None:
                extra["container_number"] = container
            if legacy_unpacking:
                extra["needs_unpacking"] = True
            if extra:
                db.execute(text("update conversation_session set collected_fields = collected_fields || cast(:x as jsonb) "
                                "where session_id = :s"), {"x": json.dumps(extra), "s": log.origin_session_id})
            db.commit()
            serial, log_id = log.serial_number, log.log_id
        finally:
            db.close()
        staff_id, wh = _staff(world, "warehouseman", [w])
        return {"serial": serial, "log_id": log_id, "warehouse": w, "staff_id": staff_id, "me": wh}
    return make


def _start_receipt(p, r):
    return _turn(p, r["me"], f"确认入库 {r['serial']}")


def _open(r, *more):
    return [_ai("new_request", "confirm_inbound_completion", reference_serial=r["serial"]), *more]


def test_receipt_with_container_requires_the_fee(receipt, monkeypatch):
    """4"""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450), _ai("confirm")))
    asked = _start_receipt(p, r)
    assert "该入库柜号 MSCU1234567，请提供拆柜费金额" in asked.reply_text
    shown = _turn(p, r["me"], "450", asked.case_number)
    assert "柜号：MSCU1234567" in shown.reply_text and "拆柜费：$450" in shown.reply_text
    _turn(p, r["me"], "确认", shown.case_number)
    res = _result(r["log_id"])
    assert res["status"] == "success"
    assert (res["result"]["unpacking_fee"], res["result"]["container_number"]) == (450, "MSCU1234567")


def test_receipt_with_container_accepts_zero(receipt, monkeypatch):
    """5"""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=0), _ai("confirm")))
    asked = _start_receipt(p, r)
    _turn(p, r["me"], "0", asked.case_number)
    _turn(p, r["me"], "确认", asked.case_number)
    assert _result(r["log_id"])["result"]["unpacking_fee"] == 0


def test_receipt_without_container_warns_and_confirms_at_zero(receipt, monkeypatch):
    """6 + 10: no 柜号 (including a legacy 需要拆包=是 request) -> the $0 warning."""
    r = receipt(legacy_unpacking=True)
    p = _processor(monkeypatch, _open(r, _ai("confirm")))
    shown = _start_receipt(p, r)
    assert "此入库无柜号，拆柜费将为 $0" in shown.reply_text
    _turn(p, r["me"], "确认", shown.case_number)
    res = _result(r["log_id"])["result"]
    assert (res["unpacking_fee"], res["container_number"]) == (0, None)


def test_warehouse_override_fee_and_container(receipt, monkeypatch):
    """7: an amount, and an amount with a 柜号, on a request without one."""
    r = receipt()
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450, container_number="MSCU1234567"), _ai("confirm")))
    shown = _start_receipt(p, r)
    updated = _turn(p, r["me"], "柜号 MSCU1234567 拆柜费 450", shown.case_number)
    assert "柜号：MSCU1234567" in updated.reply_text and "拆柜费：$450" in updated.reply_text
    _turn(p, r["me"], "确认", shown.case_number)
    res = _result(r["log_id"])["result"]
    assert (res["unpacking_fee"], res["container_number"]) == (450, "MSCU1234567")


def test_warehouse_given_container_makes_the_fee_required(receipt, monkeypatch):
    """8"""
    r = receipt("无")
    p = _processor(monkeypatch, _open(r, _ai(container_number="MSCU1234567")))
    shown = _start_receipt(p, r)
    asked = _turn(p, r["me"], "柜号 MSCU1234567", shown.case_number)
    assert "该入库柜号 MSCU1234567，请提供拆柜费金额" in asked.reply_text


def test_fee_over_cap_yes_accepts_but_never_executes(receipt, monkeypatch):
    """9 + 13b"""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=45000), _ai("confirm")))
    asked = _start_receipt(p, r)
    check = _turn(p, r["me"], "拆柜费 45000", asked.case_number)
    assert "拆柜费 $45,000 超出常见范围" in check.reply_text
    shown = _turn(p, r["me"], "是", asked.case_number)                   # no AI call
    assert "拆柜费：$45,000" in shown.reply_text
    assert _result(r["log_id"])["status"] == "processing"               # not executed
    _turn(p, r["me"], "确认", asked.case_number)
    assert _result(r["log_id"])["result"]["unpacking_fee"] == 45000


@pytest.mark.parametrize("container", ["MSCU1234567", None])
def test_invalid_correction_blocks_confirmation(receipt, monkeypatch, container):
    """15 + 17 (45.555 rounded by the AI): rejected; 确认 refused; 500 works.
    Without a 柜号, 确认 must not give $0 either."""
    r = receipt(container)
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450), _ai(unpacking_fee=45.55),
                                      _ai("confirm"), _ai(unpacking_fee=500), _ai("confirm")))
    first = _start_receipt(p, r)
    _turn(p, r["me"], "拆柜费 450", first.case_number)
    rejected = _turn(p, r["me"], "拆柜费 45.555", first.case_number)
    assert rejected.reply_text == "拆柜费金额无效：45.555（须为数字，最多两位小数），请重新输入。"
    refused = _turn(p, r["me"], "确认", first.case_number)
    assert "请先提供有效的拆柜费金额" in refused.reply_text
    assert _result(r["log_id"])["status"] == "processing"
    shown = _turn(p, r["me"], "拆柜费 500", first.case_number)
    assert "拆柜费：$500" in shown.reply_text
    _turn(p, r["me"], "确认", first.case_number)
    assert _result(r["log_id"])["result"]["unpacking_fee"] == 500


@pytest.mark.parametrize("message, ai_fee, accepted", [
    ("$450", 450, True), ("1,200", 1200, True), ("四百五", 450, False),
    ("柜号 MSCU1234567 拆柜费 450", 1234567, False), ("S2 72箱 2托，拆柜费 450", 72, False),
    ("REQ-20261008-000141 拆柜费 450", 450, True), ("300 450", 300, False),
])
def test_fee_must_match_the_message(receipt, monkeypatch, message, ai_fee, accepted):
    """17 + 18 end to end."""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=ai_fee)))
    asked = _start_receipt(p, r)
    reply = _turn(p, r["me"], message, asked.case_number).reply_text
    if accepted:
        assert f"拆柜费：${ai_fee:,}" in reply
    else:
        assert reply == "请确认拆柜费金额（如「拆柜费 450」）。"


def test_anchored_non_standard_container_at_receipt(receipt, monkeypatch):
    """19b"""
    r = receipt()
    p = _processor(monkeypatch, _open(r, _ai(container_number="XYZ123", unpacking_fee=450)))
    shown = _start_receipt(p, r)
    check = _turn(p, r["me"], "柜号 XYZ123 拆柜费 450", shown.case_number)
    assert "柜号「XYZ123」不是标准格式" in check.reply_text


# ── Batch (11) and invoice (12) ──────────────────────────────────────────────

def test_batch_leaves_out_container_requests_and_completes_others_at_zero(world, monkeypatch):
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        with_ctn = world.request(db, w, pallets(10, 1, sku), direction="inbound")
        plain_a = world.request(db, w, pallets(10, 1, sku), direction="inbound")
        plain_b = world.request(db, w, pallets(10, 1, sku), direction="inbound")
        db.execute(text("update conversation_session set collected_fields = collected_fields || "
                        "'{\"container_number\": \"MSCU1234567\"}'::jsonb where session_id = :s"),
                   {"s": with_ctn.origin_session_id})
        db.commit()
        ids = [(l.log_id, l.serial_number) for l in (with_ctn, plain_a, plain_b)]
    finally:
        db.close()
    _, wh = _staff(world, "warehouseman", [w])
    p = _processor(monkeypatch, [_ai("new_request", "confirm_inbound_completion_batch",
                                     selection={"select_all": True})])
    summary = _turn(p, wh, "全部确认入库")
    assert f"{ids[0][1]}：含柜号，需填写拆柜费，请单独确认" in summary.reply_text
    assert "柜号：无，拆柜费 $0" in summary.reply_text and "如某笔需收取拆柜费，请单独确认该笔。" in summary.reply_text
    _turn(p, wh, "确认", summary.case_number)
    assert _result(ids[0][0])["status"] == "processing"
    for log_id, _ in ids[1:]:
        res = _result(log_id)
        assert res["status"] == "success" and res["result"]["unpacking_fee"] == 0


def test_invoice_shows_container_and_fee(receipt, monkeypatch):
    from openpyxl import load_workbook
    from core.uchoice_invoice_export import build_invoice_workbook
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450), _ai("confirm")))
    asked = _start_receipt(p, r)
    _turn(p, r["me"], "450", asked.case_number)
    _turn(p, r["me"], "确认", asked.case_number)
    db = SessionLocal()
    try:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        wb = load_workbook(io.BytesIO(build_invoice_workbook(db, r["warehouse"], month)))
    finally:
        db.close()
    inbound = list(wb["Inbound"].iter_rows(values_only=True))
    assert inbound[0][4:6] == ("Container #", "Container Unpacking Fee")
    row = next(row for row in inbound[1:] if row[1] == r["serial"])
    assert row[4:6] == ("MSCU1234567", 450)
    labels = [row[0] for row in wb["Summary"].iter_rows(values_only=True)]
    assert "Container unpacking fee" in labels


@pytest.mark.parametrize("message, fee", [("拆柜费 500", 500), ("不收拆柜费", 0)])
def test_typed_correction_is_used_even_if_the_ai_misses_it(receipt, monkeypatch, message, fee):
    """Implementation audit P1: $450 entered, then a correction the AI
    doesn't extract -> the typed amount wins, and 确认 charges it."""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450), _ai(), _ai("confirm")))
    asked = _start_receipt(p, r)
    _turn(p, r["me"], "拆柜费 450", asked.case_number)
    shown = _turn(p, r["me"], message, asked.case_number)
    assert f"拆柜费：${fee}" in shown.reply_text
    _turn(p, r["me"], "确认", asked.case_number)
    assert _result(r["log_id"])["result"]["unpacking_fee"] == fee


def test_no_fee_phrase_clears_a_rejected_fee(receipt, monkeypatch):
    r = receipt()
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=45.55), _ai(), _ai("confirm")))
    first = _start_receipt(p, r)
    _turn(p, r["me"], "拆柜费 45.555", first.case_number)
    shown = _turn(p, r["me"], "不收拆柜费", first.case_number)
    assert "拆柜费：$0" in shown.reply_text
    _turn(p, r["me"], "确认", first.case_number)
    res = _result(r["log_id"])
    assert res["status"] == "success" and res["result"]["unpacking_fee"] == 0


def test_amount_only_reply_clears_a_rejected_fee_and_unrelated_text_keeps_it(receipt, monkeypatch):
    """Implementation audit round 2: `0` alone (AI missed it) clears a
    rejected fee; `不收货了` never touches the fee."""
    r = receipt("MSCU1234567")
    p = _processor(monkeypatch, _open(r, _ai(unpacking_fee=450), _ai(), _ai(unpacking_fee=45.55), _ai(), _ai("confirm")))
    first = _start_receipt(p, r)
    _turn(p, r["me"], "拆柜费 450", first.case_number)
    kept = _turn(p, r["me"], "不收货了", first.case_number)
    assert "拆柜费：$450" in kept.reply_text
    _turn(p, r["me"], "拆柜费 45.555", first.case_number)
    shown = _turn(p, r["me"], "0", first.case_number)
    assert "拆柜费：$0" in shown.reply_text
    _turn(p, r["me"], "确认", first.case_number)
    res = _result(r["log_id"])
    assert res["status"] == "success" and res["result"]["unpacking_fee"] == 0
