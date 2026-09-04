"""
Coverage for api/admin/logs.py's GET /admin/request-logs (list) and
GET /admin/request-logs/{serial_number} (detail) -- previously untested
entirely. Exercises the wechat_openid nullability fix (Kefu rows have it
NULL and previously raised a Pydantic validation error), the Kefu
display-name join, keyset pagination (including the off-by-one-page bug
found and fixed this review cycle), date-parsing timezone correctness, and
the multi-session conversation retrieval for the ledger's detail view.

Real Postgres DB, scoped to rows this file creates itself and cleans up by
exact id -- never bulk-deletes by the shared test identity.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from api.admin import logs as logs_module
from database import SessionLocal, get_db
from models.group import GroupConfig


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(logs_module.router)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


AUTH = {"X-Admin-Key": config.ADMIN_API_KEY}
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


def _real_group_id(db) -> str:
    group = db.query(GroupConfig).filter_by(wechat_group_id=WECHAT_GROUP_ID).first()
    assert group is not None, "fixture group not found -- seed data missing"
    return group.group_id


def _make_log(db, group_id, *, source_channel="smart_robot", wechat_openid=None,
              submitted_by_staff_id=None, status="success", created_at=None):
    from models.request_log import RequestLog
    log = RequestLog(
        group_id=group_id,
        status=status,
        raw_message=f"admin-logs-test-{uuid.uuid4().hex[:8]}",
        source_channel=source_channel,
        wechat_openid=wechat_openid,
        submitted_by_staff_id=submitted_by_staff_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    if created_at is not None:
        db.execute(text("update request_log set created_at = :ts where log_id = :lid"),
                   {"ts": created_at, "lid": log.log_id})
        db.commit()
        db.refresh(log)
    return log


def _make_staff(db, group_id, display_name):
    from models.kefu import KefuStaff
    from models.role import Role
    role = db.query(Role).filter_by(name="customer").one()
    staff = KefuStaff(
        open_kfid=f"kf-logstest-{uuid.uuid4().hex[:8]}",
        external_userid=f"staff-logstest-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        role_id=role.role_id,
        display_name=display_name,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _cleanup_logs(db, log_ids):
    if log_ids:
        db.execute(text("delete from conversation_session where request_log_id = any(:ids)"), {"ids": log_ids})
        db.execute(text("delete from request_log where log_id = any(:ids)"), {"ids": log_ids})
        db.commit()


def _cleanup_staff(db, staff_ids):
    if staff_ids:
        db.execute(text("delete from kefu_staff where staff_id = any(:ids)"), {"ids": staff_ids})
        db.commit()


def test_kefu_row_with_null_wechat_openid_does_not_raise(client):
    """The bug: RequestLogSummary.wechat_openid was required `str`, but Kefu
    rows genuinely store it as NULL. Confirms the fix."""
    db = SessionLocal()
    log_ids = []
    try:
        group_id = _real_group_id(db)
        log = _make_log(db, group_id, source_channel="kefu", wechat_openid=None,
                         created_at=datetime.now(timezone.utc))
        log_ids = [log.log_id]

        resp = client.get("/admin/request-logs", params={"date_from": "2020-01-01T00:00:00"}, headers=AUTH)
        assert resp.status_code == 200
        row = next(r for r in resp.json()["data"] if r["log_id"] == str(log.log_id))
        assert row["wechat_openid"] is None
        assert row["source_channel"] == "kefu"
    finally:
        _cleanup_logs(db, log_ids)
        db.close()


def test_kefu_display_name_resolves_via_staff_join(client):
    db = SessionLocal()
    log_ids, staff_ids = [], []
    try:
        group_id = _real_group_id(db)
        staff = _make_staff(db, group_id, "Test Staff Display Name")
        staff_ids = [staff.staff_id]
        log = _make_log(db, group_id, source_channel="kefu", submitted_by_staff_id=staff.staff_id,
                         created_at=datetime.now(timezone.utc))
        log_ids = [log.log_id]

        resp = client.get("/admin/request-logs", params={"date_from": "2020-01-01T00:00:00"}, headers=AUTH)
        assert resp.status_code == 200
        row = next(r for r in resp.json()["data"] if r["log_id"] == str(log.log_id))
        assert row["display_name"] == "Test Staff Display Name"
    finally:
        _cleanup_logs(db, log_ids)
        _cleanup_staff(db, staff_ids)
        db.close()


def test_invalid_status_filter_rejected(client):
    resp = client.get("/admin/request-logs", params={"status": "not_a_real_status"}, headers=AUTH)
    assert resp.status_code == 400


def test_invalid_source_channel_filter_rejected(client):
    resp = client.get("/admin/request-logs", params={"source_channel": "carrier_pigeon"}, headers=AUTH)
    assert resp.status_code == 400


def test_date_from_after_date_to_rejected(client):
    resp = client.get(
        "/admin/request-logs",
        params={"date_from": "2026-01-05T00:00:00", "date_to": "2026-01-01T00:00:00"},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_offset_aware_date_filter_converts_not_relabels(client):
    """2026-01-01T00:00:00-04:00 is 2026-01-01T04:00:00 UTC. A row created
    at 2026-01-01T02:00:00 UTC must be EXCLUDED by date_from with that
    offset (it's before the true UTC instant), proving the endpoint
    converts rather than relabels the offset."""
    db = SessionLocal()
    log_ids = []
    try:
        group_id = _real_group_id(db)
        log = _make_log(db, group_id, created_at=datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc))
        log_ids = [log.log_id]

        resp = client.get(
            "/admin/request-logs",
            params={"date_from": "2026-01-01T00:00:00-04:00", "date_to": "2026-01-02T00:00:00-04:00"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        ids = {r["log_id"] for r in resp.json()["data"]}
        assert str(log.log_id) not in ids, (
            "a naive .replace(tzinfo=utc) bug would have kept this row (relabeling -04:00 as if it were "
            "+00:00, making date_from effectively 2026-01-01T00:00:00 UTC); the correct conversion moves "
            "date_from to 2026-01-01T04:00:00 UTC, which excludes this 02:00 UTC row"
        )
    finally:
        _cleanup_logs(db, log_ids)
        db.close()


def test_malformed_cursor_rejected(client):
    resp = client.get("/admin/request-logs", params={"cursor": "not-a-real-cursor"}, headers=AUTH)
    assert resp.status_code == 400


def test_structurally_valid_cursor_with_non_uuid_log_id_rejected_not_500(client):
    """A cursor that's valid base64/JSON/ISO-datetime but carries a garbage
    log_id used to sail past _decode_cursor's own checks and reach the
    PostgreSQL UUID comparison uncaught, surfacing as a raw 500 instead of
    a clean 400."""
    import base64
    import json
    payload = json.dumps(["2026-01-01T00:00:00+00:00", "not-a-real-uuid"])
    cursor = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    resp = client.get("/admin/request-logs", params={"cursor": cursor}, headers=AUTH)
    assert resp.status_code == 400


def test_structurally_valid_cursor_with_bad_timestamp_rejected_not_500(client):
    import base64
    import json
    payload = json.dumps(["not-a-real-timestamp", str(uuid.uuid4())])
    cursor = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    resp = client.get("/admin/request-logs", params={"cursor": cursor}, headers=AUTH)
    assert resp.status_code == 400


def test_sessions_with_identical_timestamps_order_deterministically(client):
    """Two sessions created in the same instant must still order the same
    way every time -- created_at alone can tie; session_id is the
    tie-breaker."""
    db = SessionLocal()
    log_ids = []
    try:
        from models.session import ConversationSession

        group_id = _real_group_id(db)
        log = _make_log(db, group_id, source_channel="kefu")
        log_ids = [log.log_id]

        same_instant = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        s1 = ConversationSession(
            group_id=group_id, status="completed", request_log_id=log.log_id,
            source_channel="kefu", conversation_history=[{"role": "user", "content": "a"}],
        )
        s2 = ConversationSession(
            group_id=group_id, status="completed", request_log_id=log.log_id,
            source_channel="kefu", conversation_history=[{"role": "user", "content": "b"}],
        )
        db.add(s1)
        db.add(s2)
        db.commit()
        db.execute(text("update conversation_session set created_at = :ts where session_id = any(:ids)"),
                   {"ts": same_instant, "ids": [s1.session_id, s2.session_id]})
        db.commit()

        expected_order = sorted([str(s1.session_id), str(s2.session_id)])

        resp1 = client.get(f"/admin/request-logs/{log.serial_number}", headers=AUTH)
        resp2 = client.get(f"/admin/request-logs/{log.serial_number}", headers=AUTH)
        order1 = [s["session_id"] for s in resp1.json()["data"]["sessions"]]
        order2 = [s["session_id"] for s in resp2.json()["data"]["sessions"]]

        assert order1 == expected_order
        assert order2 == expected_order
    finally:
        _cleanup_logs(db, log_ids)
        db.close()


def test_pagination_covers_every_seeded_row_exactly_once(client):
    """The exact test that would have caught encoding the probe row instead
    of the last-returned row as next_cursor: seed a count that isn't an
    exact multiple of page_size, walk every page, assert the union of all
    pages equals every seeded row exactly once."""
    db = SessionLocal()
    log_ids = []
    try:
        group_id = _real_group_id(db)
        base = datetime(2026, 2, 1, tzinfo=timezone.utc)
        seeded = []
        for i in range(7):  # not a multiple of page_size=3
            log = _make_log(db, group_id, created_at=base + timedelta(seconds=i))
            seeded.append(log.log_id)
        log_ids = list(seeded)

        seen = []
        cursor = None
        for _ in range(20):  # generous upper bound on page count
            params = {"date_from": "2026-02-01T00:00:00", "date_to": "2026-02-01T00:10:00", "page_size": 3}
            if cursor:
                params["cursor"] = cursor
            resp = client.get("/admin/request-logs", params=params, headers=AUTH)
            assert resp.status_code == 200
            body = resp.json()
            seen.extend(r["log_id"] for r in body["data"] if r["log_id"] in {str(x) for x in seeded})
            cursor = body["next_cursor"]
            if cursor is None:
                break
        else:
            pytest.fail("pagination did not terminate within 20 pages")

        assert sorted(seen) == sorted(str(x) for x in seeded), (
            "every seeded row must appear exactly once across all pages -- "
            "duplicates or gaps indicate a cursor/keyset bug"
        )
    finally:
        _cleanup_logs(db, log_ids)
        db.close()


def test_detail_includes_every_session_touching_the_request_in_order(client):
    db = SessionLocal()
    log_ids, session_ids = [], []
    try:
        from models.session import ConversationSession

        group_id = _real_group_id(db)
        log = _make_log(db, group_id, source_channel="kefu")
        log_ids = [log.log_id]

        # Explicit, distinct created_at values -- server_default now() gives
        # BOTH rows the same transaction-start timestamp when flushed within
        # one uncommitted transaction (not wall-clock time per statement),
        # which would make the ordering depend on the session_id tie-breaker
        # (a random UUID) rather than the chronological order this test
        # actually wants to verify.
        s1 = ConversationSession(
            group_id=group_id, status="completed", request_log_id=log.log_id,
            source_channel="kefu", conversation_history=[{"role": "user", "content": "first"}],
        )
        s2 = ConversationSession(
            group_id=group_id, status="cancelled", request_log_id=log.log_id,
            source_channel="kefu", conversation_history=[{"role": "user", "content": "second"}],
        )
        db.add(s1)
        db.add(s2)
        db.commit()
        session_ids = [s1.session_id, s2.session_id]
        db.execute(text("update conversation_session set created_at = :ts where session_id = :sid"),
                   {"ts": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc), "sid": s1.session_id})
        db.execute(text("update conversation_session set created_at = :ts where session_id = :sid"),
                   {"ts": datetime(2026, 4, 1, 12, 0, 1, tzinfo=timezone.utc), "sid": s2.session_id})
        db.commit()

        resp = client.get(f"/admin/request-logs/{log.serial_number}", headers=AUTH)
        assert resp.status_code == 200
        sessions = resp.json()["data"]["sessions"]
        assert [s["session_id"] for s in sessions] == [str(s1.session_id), str(s2.session_id)]
        assert sessions[0]["conversation_history"] == [{"role": "user", "content": "first"}]
        assert sessions[1]["status"] == "cancelled"
    finally:
        _cleanup_logs(db, log_ids)
        db.close()


def test_detail_with_no_sessions_returns_empty_list_not_error(client):
    db = SessionLocal()
    log_ids = []
    try:
        group_id = _real_group_id(db)
        log = _make_log(db, group_id)
        log_ids = [log.log_id]

        resp = client.get(f"/admin/request-logs/{log.serial_number}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"]["sessions"] == []
    finally:
        _cleanup_logs(db, log_ids)
        db.close()
