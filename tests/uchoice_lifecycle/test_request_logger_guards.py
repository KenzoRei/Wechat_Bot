"""
core.request_logger.mark_success's status guard. Direct regression test for
the bug an external review found: mark_success previously overwrote a
target's status unconditionally, so core/workflow_engine.py's unconditional
call to it after ANY targets_existing_request workflow (including a
cancellation, which has awaits_completion=false, same as most services)
would silently revert a just-committed 'cancelled' status back to
'success' -- on the Smart Bot channel specifically, since Kefu's own
equivalent status-setting code already had this guard from the start.

Mock-based (no real DB needed) -- mark_success's only DB interaction is a
single filter_by(log_id=...).first() lookup and attribute writes.
"""
from types import SimpleNamespace

from core import request_logger


class _FakeQuery:
    def __init__(self, log):
        self._log = log

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._log


class _FakeDB:
    def __init__(self, log):
        self._log = log
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._log)

    def commit(self):
        self.committed = True


def _log(status, result=None):
    return SimpleNamespace(status=status, result=result, completed_at=None)


def test_mark_success_advances_a_processing_log():
    log = _log("processing")
    db = _FakeDB(log)
    request_logger.mark_success(db, "log-1", {"tracking": "abc"})
    assert log.status == "success"
    assert log.result == {"tracking": "abc"}
    assert log.completed_at is not None
    assert db.committed is True


def test_mark_success_never_overwrites_a_cancelled_log():
    """
    The exact regression: a cancellation workflow step already moved this
    (identity-mapped, same object) log to 'cancelled' and set its result
    earlier in the same call chain -- core/workflow_engine.py's later
    unconditional mark_success() call must not revert it.
    """
    log = _log("cancelled", result={})
    db = _FakeDB(log)
    request_logger.mark_success(db, "log-1", {"should": "not persist"})
    assert log.status == "cancelled"
    assert log.result == {}
    assert db.committed is False


def test_mark_success_never_overwrites_an_already_successful_log():
    log = _log("success", result={"original": True})
    db = _FakeDB(log)
    request_logger.mark_success(db, "log-1", {"should": "not persist"})
    assert log.status == "success"
    assert log.result == {"original": True}
    assert db.committed is False


def test_mark_success_never_overwrites_a_failed_log():
    log = _log("failed", result=None)
    db = _FakeDB(log)
    request_logger.mark_success(db, "log-1", {"should": "not persist"})
    assert log.status == "failed"
    assert db.committed is False
