"""
Batch cancellation (cancel_*_request_batch, V40) against real PostgreSQL.
Plan: docs/ai-collaboration/2026-10-cancel-batch/plan.md -- test numbers in
the docstrings refer to its Tests section.

Isolation: synthetic warehouse codes and SKUs via the batch-completion
World fixture; cleanup is by the exact IDs each test created.
"""
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from ai.base import AIResponse
from core.kefu_contracts import CaseTurnSuccess, KefuIdentity
from database import SessionLocal
from tests.kefu_integration.test_completion_batch import World, pallets, status


@pytest.fixture
def world():
    w = World()
    yield w
    w.cleanup()


def _staff(world, db, role_name, display_name=None):
    from models.kefu import KefuStaff
    from models.role import Role
    staff = KefuStaff(
        open_kfid=f"kf-cb-{world.tag}", external_userid=f"cb-{uuid.uuid4().hex[:8]}",
        group_id=world.group_id(db), role_id=db.query(Role).filter_by(name=role_name).one().role_id,
        display_name=display_name,
    )
    db.add(staff)
    db.flush()
    world.staff_ids.append(staff.staff_id)
    return staff


def _ai(monkeypatch, responses):
    """Scripted AI; deterministic turns call nothing, so an exhausted script
    fails the test loudly."""
    import core.kefu_case_adapter as adapter
    script = iter(responses)
    monkeypatch.setattr(adapter._ai_chain, "process", lambda context: next(script))
    monkeypatch.setattr(adapter, "_direct_send", lambda *a, **k: None)
    return adapter.make_case_turn_processor(client=None, db_factory=SessionLocal)


def _turn(processor, identity, content, case_number=None, voice=False):
    meta = {"msgid": f"cb-{uuid.uuid4().hex}"}
    if voice:
        meta["input_modality"] = "voice"
    return processor(identity=identity, message_content=content, message_meta=meta, case_number_hint=case_number)


def _ask(service="cancel_outbound_request", selection=None, intent="new_request"):
    fields = {"selection": selection} if selection is not None else {}
    return AIResponse(intent=intent, service_type_name=service, extracted_fields=fields,
                      all_fields_collected=False, reply="")


def _requests(world, owner_staff_id, n, direction="outbound"):
    """n processing requests created by owner_staff_id through Kefu."""
    db = SessionLocal()
    try:
        w, sku = world.warehouse(), world.sku(db)
        logs = [world.request(db, w, pallets(10, 1, sku), direction=direction, staff_id=owner_staff_id)
                for _ in range(n)]
        db.commit()
        return [(l.log_id, l.serial_number) for l in logs]
    finally:
        db.close()


def _owner(world, role="customer", name=None):
    db = SessionLocal()
    try:
        staff = _staff(world, db, role, name)
        db.commit()
        return staff.staff_id, KefuIdentity(staff.open_kfid, staff.external_userid)
    finally:
        db.close()


def _statuses(reqs):
    db = SessionLocal()
    try:
        return [status(db, log_id) for log_id, _ in reqs]
    finally:
        db.close()


# ── 1, 13: list → "1 3" → summary → 确认 ────────────────────────────────────

def test_list_numbers_summary_confirm_cancels_exactly_the_selection(world, monkeypatch):
    staff_id, me = _owner(world, name="Harry")
    reqs = _requests(world, staff_id, 3)
    processor = _ai(monkeypatch, [_ask()])

    listed = _turn(processor, me, "取消出库")
    assert "回复编号取消，可多选" in listed.reply_text

    summary = _turn(processor, me, "1 3", listed.case_number)
    assert "批量取消出库申请（共 2 笔）" in summary.reply_text
    assert "取消后无法恢复" in summary.reply_text and "放弃" in summary.reply_text
    assert "创建：Harry" in summary.reply_text

    done = _turn(processor, me, "确认", summary.case_number)
    assert "已取消 2 笔出库申请" in done.reply_text
    assert f"{reqs[0][1]}（Harry）" in done.reply_text          # 13 (Q2)
    assert _statuses(reqs) == ["cancelled", "processing", "cancelled"]


# ── 2: partial confirm at the summary ────────────────────────────────────────

def test_partial_reply_cancels_only_the_chosen(world, monkeypatch):
    staff_id, me = _owner(world)
    reqs = _requests(world, staff_id, 3)
    processor = _ai(monkeypatch, [_ask("cancel_outbound_request_batch", {"select_all": True})])
    summary = _turn(processor, me, "全部取消出库")
    assert "共 3 笔" in summary.reply_text
    _turn(processor, me, "①③", summary.case_number)
    assert _statuses(reqs) == ["cancelled", "processing", "cancelled"]


# ── 3: a selection of one stays single ───────────────────────────────────────

def test_one_selected_keeps_the_single_cancel_flow(world, monkeypatch):
    staff_id, me = _owner(world)
    reqs = _requests(world, staff_id, 3)
    processor = _ai(monkeypatch, [_ask()])
    listed = _turn(processor, me, "取消出库")
    single = _turn(processor, me, "2", listed.case_number)
    assert "批量取消" not in single.reply_text and reqs[1][1] in single.reply_text
    assert "取消出库申请" in single.reply_text


# ── 4: D2 -- a request leaves 'processing' before 确认 ───────────────────────

def test_request_completed_before_confirm_cancels_nothing_and_rerenders(world, monkeypatch):
    staff_id, me = _owner(world)
    reqs = _requests(world, staff_id, 2)
    processor = _ai(monkeypatch, [_ask("cancel_outbound_request_batch", {"select_all": True})])
    summary = _turn(processor, me, "全部取消出库")
    db = SessionLocal()
    db.execute(text("update request_log set status='success' where log_id=:id"), {"id": reqs[0][0]})
    db.commit()
    db.close()

    blocked = _turn(processor, me, "确认", summary.case_number)
    assert "已被仓库确认完成" in blocked.reply_text and "本次未执行任何操作" in blocked.reply_text
    assert _statuses(reqs) == ["success", "processing"]
    _turn(processor, me, "确认", blocked.case_number)
    assert _statuses(reqs) == ["success", "cancelled"]


# ── 5: a non-admin can't cancel someone else's request ───────────────────────

def test_non_admin_selection_of_another_users_request_is_dropped(world, monkeypatch):
    staff_id, me = _owner(world)
    other_id, _ = _owner(world)
    mine = _requests(world, staff_id, 2)
    theirs = _requests(world, other_id, 1)
    processor = _ai(monkeypatch, [_ask("cancel_outbound_request_batch",
                                       {"serials": [mine[0][1], mine[1][1], theirs[0][1]]})])
    summary = _turn(processor, me, "取消这三笔")
    assert "共 2 笔" in summary.reply_text and f"{theirs[0][1]} 不在可取消的待处理申请中" in summary.reply_text
    _turn(processor, me, "确认", summary.case_number)
    assert _statuses(mine + theirs) == ["cancelled", "cancelled", "processing"]


def test_locked_recheck_rejects_another_users_request_even_if_forced(world):
    from handlers.uchoice.cancel_batch import RunCancellationBatchHandler
    staff_id, _ = _owner(world)
    other_id, _ = _owner(world)
    mine = _requests(world, staff_id, 1)
    theirs = _requests(world, other_id, 1)
    db = SessionLocal()
    try:
        context = {"source_channel": "kefu", "role": "customer", "submitted_by_staff_id": str(staff_id),
                   "group_id": str(world.group_id(db)), "wechat_openid": None,
                   "collected_fields": {"reference_serials": [mine[0][1], theirs[0][1]]}}
        result = RunCancellationBatchHandler().handle(context, {"direction": "outbound"}, db)
        db.commit()
    finally:
        db.close()
    assert result["_kefu_stop_workflow"] == "batch_blocked"
    assert "没有权限" in result["batch_blocked"]["message"]
    assert _statuses(mine + theirs) == ["processing", "processing"]


# ── 6: notifications per original requester ──────────────────────────────────

def test_admin_cancel_notifies_each_original_requester(world):
    from handlers.uchoice.cancel_batch import RunCancellationBatchHandler
    creator_id, _ = _owner(world)
    admin_id, _ = _owner(world, role="admin")
    kefu_made = _requests(world, creator_id, 1)
    robot_made = _requests(world, creator_id, 1)
    own = _requests(world, admin_id, 1)
    db = SessionLocal()
    group_id = world.group_id(db)
    webhook_before = db.execute(text("select group_robot_webhook_url from group_config where group_id=:g"),
                                {"g": group_id}).scalar()
    try:
        db.execute(text("update request_log set source_channel='smart_robot', submitted_by_staff_id=null, "
                        "wechat_openid='o-cancel-batch-test' where log_id=:id"), {"id": robot_made[0][0]})
        db.execute(text("update group_config set group_robot_webhook_url='https://example.invalid/hook' "
                        "where group_id=:g"), {"g": group_id})
        db.commit()
        context = {"source_channel": "kefu", "role": "admin", "submitted_by_staff_id": str(admin_id),
                   "group_id": str(group_id), "wechat_openid": None,
                   "collected_fields": {"reference_serials": [kefu_made[0][1], robot_made[0][1], own[0][1]]}}
        result = RunCancellationBatchHandler().handle(context, {"direction": "outbound"}, db)
        db.commit()
        queued = db.execute(text("select count(*) from kefu_outbound_delivery where recipient_staff_id=:s "
                                 "and idempotency_key like 'request-cancelled:%'"), {"s": creator_id}).scalar()
        to_admin = db.execute(text("select count(*) from kefu_outbound_delivery where recipient_staff_id=:s"),
                              {"s": admin_id}).scalar()
    finally:
        db.execute(text("update group_config set group_robot_webhook_url=:u where group_id=:g"),
                   {"u": webhook_before, "g": group_id})
        db.commit()
        db.close()
    assert len(result["batch_cancelled"]) == 3
    assert queued == 1                                   # the Kefu-created one
    assert to_admin == 0                                 # self-cancel: no notice
    deferred = context["_deferred_webhook_notifications"]
    assert len(deferred) == 1 and robot_made[0][1] in deferred[0]["content"]


# ── 7: overlapping concurrent batches ────────────────────────────────────────

def test_overlapping_concurrent_batches_do_not_deadlock(world):
    from handlers.uchoice.cancel_batch import RunCancellationBatchHandler
    staff_id, _ = _owner(world, role="admin")
    a, b, c = _requests(world, staff_id, 3)
    db = SessionLocal()
    group_id = str(world.group_id(db))
    db.close()

    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(serials):
        wdb = SessionLocal()
        try:
            context = {"source_channel": "kefu", "role": "admin", "submitted_by_staff_id": str(staff_id),
                       "group_id": group_id, "wechat_openid": None,
                       "collected_fields": {"reference_serials": serials}}
            barrier.wait(timeout=10)
            results.append(RunCancellationBatchHandler().handle(context, {"direction": "outbound"}, wdb))
            wdb.commit()
        except Exception as exc:
            wdb.rollback()
            errors.append(exc)
        finally:
            wdb.close()

    threads = [threading.Thread(target=worker, args=(s,)) for s in ([a[1], b[1]], [c[1], b[1]])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    applied = [r for r in results if "batch_cancelled" in r]
    blocked = [r for r in results if r.get("_kefu_stop_workflow") == "batch_blocked"]
    assert len(applied) == 1 and len(blocked) == 1
    assert sorted(_statuses([a, b, c])).count("cancelled") == 2


# ── 8, 9: abandon and voice ──────────────────────────────────────────────────

def test_abandon_at_summary_cancels_nothing(world, monkeypatch):
    staff_id, me = _owner(world)
    reqs = _requests(world, staff_id, 2)
    processor = _ai(monkeypatch, [
        _ask("cancel_outbound_request_batch", {"select_all": True}),
        _ask(None, intent="cancel"),
    ])
    summary = _turn(processor, me, "全部取消出库")
    abandoned = _turn(processor, me, "放弃", summary.case_number)
    assert abandoned.reply_text == "已放弃批量取消，未取消任何申请。"
    assert _statuses(reqs) == ["processing", "processing"]


def test_voice_confirm_never_cancels(world, monkeypatch):
    staff_id, me = _owner(world)
    reqs = _requests(world, staff_id, 2)
    processor = _ai(monkeypatch, [
        _ask("cancel_outbound_request_batch", {"select_all": True}),
        _ask(None, intent="confirm"),
    ])
    summary = _turn(processor, me, "全部取消出库")
    _turn(processor, me, "确认", summary.case_number, voice=True)
    assert _statuses(reqs) == ["processing", "processing"]


# ── 11: a failed notice never undoes a cancellation (R1) ─────────────────────

def _bad_sql(db, *args, **kwargs):
    db.execute(text("select * from no_such_table_cancel_batch_test"))


def _failing_cancel_notices(monkeypatch):
    """enqueue_text fails with a database error for cancellation notices
    only; every other delivery (the turn's own reply) works normally."""
    import core.kefu_delivery
    original = core.kefu_delivery.enqueue_text

    def enqueue(db, **kw):
        if kw.get("idempotency_key", "").startswith("request-cancelled:"):
            _bad_sql(db)
        return original(db, **kw)
    monkeypatch.setattr(core.kefu_delivery, "enqueue_text", enqueue)


@pytest.mark.parametrize("patch_target", ["lookup", "insert"])
def test_failed_notice_never_undoes_the_batch(world, monkeypatch, patch_target):
    creator_id, _ = _owner(world)
    admin_id, admin = _owner(world, role="admin")
    reqs = _requests(world, creator_id, 2)
    if patch_target == "lookup":
        from handlers.uchoice.cancel_request import NotifyCancelledRequestHandler
        monkeypatch.setattr(NotifyCancelledRequestHandler, "_notify_kefu",
                            staticmethod(lambda context, db, target, content: _bad_sql(db)))
    else:
        _failing_cancel_notices(monkeypatch)
    processor = _ai(monkeypatch, [_ask("cancel_outbound_request_batch", {"select_all": True})])
    summary = _turn(processor, admin, "全部取消出库")
    done = _turn(processor, admin, "确认", summary.case_number)
    assert isinstance(done, CaseTurnSuccess) and "已取消 2 笔出库申请" in done.reply_text
    assert _statuses(reqs) == ["cancelled", "cancelled"]


def test_failed_notice_never_undoes_a_single_cancel(world, monkeypatch):
    creator_id, _ = _owner(world)
    _, admin = _owner(world, role="admin")
    reqs = _requests(world, creator_id, 1)
    _failing_cancel_notices(monkeypatch)
    processor = _ai(monkeypatch, [_ask(), _ask(None, intent="confirm")])
    summary = _turn(processor, admin, "取消出库")
    _turn(processor, admin, "确认", summary.case_number)
    assert _statuses(reqs) == ["cancelled"]


# ── 12: a V40 rerun preserves revocations (R2) ───────────────────────────────

def test_v40_rerun_does_not_restore_revoked_grants_or_groups():
    import importlib.util
    import os
    import psycopg2

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("apply_migrations", root / "scripts" / "apply_migrations.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    sql = runner._strip_psql_meta_commands(
        (root / "db" / "migrations" / "V40__cancel_request_batch.sql").read_text(encoding="utf-8")
    )
    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    try:
        cur = conn.cursor()
        cur.execute("select rsp.role_id, rsp.service_type_id, rsp.created_by from role_service_permission rsp "
                    "join service_type st using(service_type_id) where st.name='cancel_outbound_request_batch' limit 1")
        grant = cur.fetchone()
        cur.execute("select gs.group_id, gs.service_type_id, gs.workflow_id, gs.config::text from group_service gs "
                    "join service_type st using(service_type_id) where st.name='cancel_outbound_request_batch' limit 1")
        enabled = cur.fetchone()
        assert grant and enabled
        cur.execute("delete from role_service_permission where role_id=%s and service_type_id=%s", grant[:2])
        cur.execute("delete from group_service where group_id=%s and service_type_id=%s", enabled[:2])
        cur.execute("update service_type set description='stale' where name='cancel_outbound_request_batch'")
        conn.commit()

        cur.execute(sql)                     # the rerun
        conn.commit()
        cur.execute("select count(*) from role_service_permission where role_id=%s and service_type_id=%s", grant[:2])
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from group_service where group_id=%s and service_type_id=%s", enabled[:2])
        assert cur.fetchone()[0] == 0
        cur.execute("select description from service_type where name='cancel_outbound_request_batch'")
        assert cur.fetchone()[0] != "stale"
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("insert into role_service_permission (role_id, service_type_id, created_by) values (%s, %s, %s) "
                    "on conflict do nothing", grant)
        cur.execute("insert into group_service (group_id, service_type_id, workflow_id, config) "
                    "values (%s, %s, %s, %s::jsonb) on conflict do nothing", enabled)
        conn.commit()
        conn.close()


# ── 14: batch-only permission (R3) ───────────────────────────────────────────

def test_batch_only_grant_sees_the_list_and_can_cancel(world, monkeypatch):
    db = SessionLocal()
    role_name = f"t_cb_{uuid.uuid4().hex[:6]}"
    try:
        role_id = db.execute(text("insert into role (name) values (:n) returning role_id"), {"n": role_name}).scalar()
        db.execute(text(
            "insert into role_service_permission (role_id, service_type_id, created_by) "
            "select :r, service_type_id, 'test' from service_type where name='cancel_outbound_request_batch'"
        ), {"r": role_id})
        db.commit()
    finally:
        db.close()
    try:
        staff_id, me = _owner(world, role=role_name)
        reqs = _requests(world, staff_id, 2)
        processor = _ai(monkeypatch, [_ask("cancel_outbound_request_batch", None)])
        listed = _turn(processor, me, "取消出库")
        assert "请问要取消哪几笔出库申请" in listed.reply_text
        summary = _turn(processor, me, "全部", listed.case_number)
        _turn(processor, me, "确认", summary.case_number)
        assert _statuses(reqs) == ["cancelled", "cancelled"]
    finally:
        db = SessionLocal()
        db.execute(text("delete from role_service_permission where role_id=:r"), {"r": role_id})
        db.execute(text("update kefu_staff set role_id=(select role_id from role where name='customer') "
                        "where role_id=:r"), {"r": role_id})
        db.execute(text("delete from role where role_id=:r"), {"r": role_id})
        db.commit()
        db.close()


def test_single_cancel_by_admin_queues_the_creators_notice(world, monkeypatch):
    """Regression: _notify_kefu passed request_log_id as a str, so
    enqueue_text's read-back never matched and raised
    idempotency_key_collision on every notice. The old catch-and-print kept
    the row; the savepoint (R1) would have dropped it. A UUID fixes both."""
    creator_id, _ = _owner(world)
    _, admin = _owner(world, role="admin")
    reqs = _requests(world, creator_id, 1)
    processor = _ai(monkeypatch, [_ask(), _ask(None, intent="confirm")])
    summary = _turn(processor, admin, "取消出库")
    _turn(processor, admin, "确认", summary.case_number)
    db = SessionLocal()
    try:
        queued = db.execute(text("select count(*) from kefu_outbound_delivery where recipient_staff_id=:s "
                                 "and idempotency_key like 'request-cancelled:%'"), {"s": creator_id}).scalar()
    finally:
        db.close()
    assert _statuses(reqs) == ["cancelled"] and queued == 1
