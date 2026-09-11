"""
core/result_message.py's fedex_label/ups_label success reply: the download
link must be Smart-Robot-only now that Kefu attaches the label PDF itself
as a native chat file (core/kefu_turn_apply.py's _workflow_steps) -- showing
both would be redundant. Pure function, no DB access needed.
"""
from core import result_message


def _context(source_channel: str, **result_overrides) -> dict:
    result = {"tracking_number": "TRACK1", "label_base64": "QkFTRTY0"}
    result.update(result_overrides)
    return {"result": result, "source_channel": source_channel, "serial_number": "REQ-1"}


def test_smart_robot_keeps_download_link():
    sections = result_message.build_result_sections("fedex_label", _context("smart_robot"), None)
    items = sections[0]["items"]
    assert any("点击下载标签" in item for item in items)


def test_kefu_omits_download_link():
    sections = result_message.build_result_sections("fedex_label", _context("kefu"), None)
    items = sections[0]["items"]
    assert not any("点击下载标签" in item for item in items)
    assert any("标签追踪号" in item for item in items)


def test_no_label_base64_never_shows_link_on_either_channel():
    """Regression guard: has_label already gated this before the channel
    check was added -- must still gate it now, not just the channel."""
    for channel in ("smart_robot", "kefu"):
        sections = result_message.build_result_sections("fedex_label", _context(channel, label_base64=""), None)
        items = sections[0]["items"]
        assert not any("点击下载标签" in item for item in items)


def test_oms_work_order_still_shown_regardless_of_channel():
    for channel in ("smart_robot", "kefu"):
        sections = result_message.build_result_sections(
            "fedex_label", _context(channel, oms_work_order="WO-1"), None,
        )
        items = sections[0]["items"]
        assert any("OMS工单号：WO-1" in item for item in items)
