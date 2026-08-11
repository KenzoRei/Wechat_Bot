"""
kefu-migration-plan.md Sec 7 / Codex round-90 finding 6 -- pending-
completion-notice text formatting. Mock DB only, covering the pure-Python
labeling logic.

lock_pending_completion_notice()'s actual locking/concurrency-safety
(SELECT ... FOR UPDATE SKIP LOCKED) is NOT covered here -- mocking raw SQL
faithfully enough to prove real row-lock behavior isn't meaningful; that
guarantee is proven by a real-Postgres concurrency test instead (see
tests/kefu_integration/), per Codex's own explicit request that "the
sequential mock cannot prove it."
"""
from types import SimpleNamespace

from core import kefu_completion_notice


class _Query:
    def __init__(self, db, model):
        self.db = db
        self._service_type_id = None

    def filter_by(self, **kwargs):
        self._service_type_id = kwargs.get("service_type_id")
        return self

    def first(self):
        return self.db.service_types.get(self._service_type_id)


class MockDB:
    def __init__(self, service_types=None):
        self.service_types = service_types or {}

    def query(self, model):
        return _Query(self, model)


SERVICE_TYPES = {
    "svc-inbound": SimpleNamespace(name="uchoice_inbound_request"),
    "svc-outbound": SimpleNamespace(name="uchoice_outbound_request"),
}


def test_inbound_completion_labeled_correctly():
    db = MockDB(service_types=SERVICE_TYPES)
    log = SimpleNamespace(serial_number="REQ-1", service_type_id="svc-inbound")
    text = kefu_completion_notice.notice_text(db, log)
    assert "REQ-1" in text
    assert "入库" in text


def test_outbound_completion_labeled_correctly():
    db = MockDB(service_types=SERVICE_TYPES)
    log = SimpleNamespace(serial_number="REQ-2", service_type_id="svc-outbound")
    text = kefu_completion_notice.notice_text(db, log)
    assert "出库" in text


def test_unknown_service_type_falls_back_to_generic_label():
    db = MockDB(service_types=SERVICE_TYPES)
    log = SimpleNamespace(serial_number="REQ-3", service_type_id="svc-unknown")
    text = kefu_completion_notice.notice_text(db, log)
    assert "REQ-3" in text
    assert "请求" in text


def test_null_service_type_id_falls_back_to_generic_label():
    db = MockDB(service_types=SERVICE_TYPES)
    log = SimpleNamespace(serial_number="REQ-4", service_type_id=None)
    text = kefu_completion_notice.notice_text(db, log)
    assert "REQ-4" in text
