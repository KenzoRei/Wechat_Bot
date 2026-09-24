"""
CompleteExistingRequestHandler (single completion) pushes the "申请已完成"
notice to the original request's group only for Smart-Robot-created
requests. Kefu-created requests rely on Kefu's pull notice instead -- the
same rule as the batch completion. Offline: a fake DB, and the conftest
already turns any unexpected real webhook call into a failure.
"""
from types import SimpleNamespace

from handlers.uchoice.complete_request import CompleteExistingRequestHandler


class _FakeDB:
    def query(self, _model):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return SimpleNamespace(group_robot_webhook_url="https://hook")


def _context(source_channel):
    return {"_uchoice_target": {
        "group_id": "g", "serial_number": "REQ-1-000001", "direction": "outbound",
        "source_channel": source_channel,
    }}


def test_smart_robot_request_is_pushed_to_its_group(monkeypatch):
    sent = []
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_message", lambda url, content: sent.append((url, content)))
    CompleteExistingRequestHandler().handle(_context("smart_robot"), {}, _FakeDB())
    assert sent == [("https://hook", "✅ 您的出库申请已完成\n申请编号：REQ-1-000001\n如有问题请联系管理员。")]


def test_kefu_request_is_not_pushed(monkeypatch):
    sent = []
    monkeypatch.setattr("clients.wechat_client.send_group_webhook_message", lambda url, content: sent.append((url, content)))
    CompleteExistingRequestHandler().handle(_context("kefu"), {}, _FakeDB())
    assert sent == []
