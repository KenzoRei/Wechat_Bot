"""
Row-level locking (core/workflow_engine.py Part 2 Fix 3): a completion
attempt and a cancellation attempt racing the same 'processing' request must
serialize so exactly one wins -- never both, and never a stale in-memory
read defeating the lock.

Each worker deliberately preloads the target row via a plain (unlocked)
query in its OWN Session, before synchronizing at the barrier and only then
performing its locked read in that SAME Session -- exercising the
populate_existing() fix specifically. Staleness only matters when the SAME
Session that later does the locked read already had the row cached; a
shared/third preload session (an earlier draft of this test used one, then
closed it) leaves neither worker's own Session with a stale identity-map
entry, so the test would still pass even with populate_existing() removed.

Runs against a disposable PostgreSQL database only -- see tests/conftest.py
(auto-marked postgres, auto-skipped without TEST_DATABASE_URL).
"""
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from database import SessionLocal
from models.request_log import RequestLog
from core.workflow_errors import TargetAlreadyResolvedError
from handlers.uchoice.lookup_validate import LookupAndValidateCompletionHandler
from handlers.uchoice.cancel_request import LookupAndValidateCancellationHandler


def _real_group_and_inbound_service(db):
    from models.group import GroupConfig
    from models.service import ServiceType

    group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
    assert group is not None, "no group_config row found -- seed data missing"
    service = db.query(ServiceType).filter_by(name="uchoice_inbound_request").first()
    assert service is not None, "uchoice_inbound_request service_type not found -- seed data missing"
    return group.group_id, service.service_type_id


def _cleanup(log_id):
    if not log_id:
        return
    db = SessionLocal()
    try:
        db.execute(text("delete from request_log where log_id=:id"), {"id": log_id})
        db.commit()
    finally:
        db.close()


def test_completion_and_cancellation_race_exactly_one_wins():
    setup_db = SessionLocal()
    log_id = None
    try:
        group_id, service_type_id = _real_group_and_inbound_service(setup_db)
        openid = f"race-test-{uuid.uuid4().hex[:8]}"
        log = RequestLog(
            wechat_openid=openid,
            group_id=group_id,
            service_type_id=service_type_id,
            status="processing",
            raw_message="concurrency test",
            source_channel="smart_robot",
        )
        setup_db.add(log)
        setup_db.commit()
        setup_db.refresh(log)
        log_id = log.log_id
        setup_db.close()

        barrier = threading.Barrier(2)
        outcomes = {}
        errors = []

        def run_completion():
            db = SessionLocal()
            try:
                # Preload in THIS worker's own Session -- the same one that
                # performs the locked read below -- before synchronizing,
                # so that read has a stale cached copy to (incorrectly)
                # return without populate_existing().
                preloaded = db.query(RequestLog).filter_by(log_id=log_id).first()
                assert preloaded.status == "processing"

                barrier.wait(timeout=10)
                context = {"request_log_id": str(log_id), "warehouse_codes": None}
                # LookupAndValidateCompletionHandler only locks/validates --
                # the real pipeline's later storage/mark_success steps are
                # what actually advance status. Simulate that terminal
                # transition here (status='success') so this test can
                # actually observe "exactly one winner": without it, BOTH
                # threads would see 'processing' through the lock and both
                # would "succeed", since this handler alone never mutates
                # status.
                LookupAndValidateCompletionHandler().handle(context, {"direction": "inbound"}, db)
                target = db.query(RequestLog).filter_by(log_id=log_id).first()
                target.status = "success"
                target.completed_at = datetime.now(timezone.utc)
                db.commit()
                outcomes["completion"] = "success"
            except TargetAlreadyResolvedError as e:
                db.rollback()
                outcomes["completion"] = f"already_resolved:{e.current_status}"
            except Exception as exc:
                db.rollback()
                errors.append(("completion", exc))
            finally:
                db.close()

        def run_cancellation():
            db = SessionLocal()
            try:
                preloaded = db.query(RequestLog).filter_by(log_id=log_id).first()
                assert preloaded.status == "processing"

                barrier.wait(timeout=10)
                context = {
                    "request_log_id": str(log_id),
                    "group_id": str(group_id),
                    "role": "admin",
                    "source_channel": "smart_robot",
                    "wechat_openid": "admin-actor",
                }
                LookupAndValidateCancellationHandler().handle(context, {"direction": "inbound"}, db)
                target = db.query(RequestLog).filter_by(log_id=log_id).first()
                target.status = "cancelled"
                target.completed_at = datetime.now(timezone.utc)
                db.commit()
                outcomes["cancellation"] = "success"
            except TargetAlreadyResolvedError as e:
                db.rollback()
                outcomes["cancellation"] = f"already_resolved:{e.current_status}"
            except Exception as exc:
                db.rollback()
                errors.append(("cancellation", exc))
            finally:
                db.close()

        threads = [
            threading.Thread(target=run_completion),
            threading.Thread(target=run_cancellation),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert all(not t.is_alive() for t in threads)
        assert errors == [], f"unexpected exceptions: {errors}"

        # Exactly one attempt succeeded; the other observed a real,
        # non-stale terminal status via the locked, refreshed read --
        # never both "success", and the loser's observed status must be
        # the winner's real terminal state, not the pre-race 'processing'
        # value it would see if populate_existing() were missing.
        successes = [k for k, v in outcomes.items() if v == "success"]
        assert len(successes) == 1, f"expected exactly one winner, got: {outcomes}"

        loser_key = "cancellation" if successes[0] == "completion" else "completion"
        loser_outcome = outcomes[loser_key]
        assert loser_outcome.startswith("already_resolved:"), outcomes

        # The winner's terminal status must be exactly what's durably in
        # the DB, and the loser's locked read must have observed that same
        # real value -- not a stale 'processing' it cached before the race
        # (the actual populate_existing() regression this test targets).
        expected_status = "success" if successes[0] == "completion" else "cancelled"
        verify_db = SessionLocal()
        try:
            final = verify_db.query(RequestLog).filter_by(log_id=log_id).first()
            assert final.status == expected_status
            assert loser_outcome == f"already_resolved:{expected_status}"
        finally:
            verify_db.close()
    finally:
        _cleanup(log_id)
