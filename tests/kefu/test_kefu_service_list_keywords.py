"""check_services now doubles as "how do I ask for this" documentation --
each entry shows up to 2 example trigger keywords alongside its label."""
from core.kefu_outcomes import ServiceListEntry, ServiceListOutcome
from core.kefu_response_renderer import render_kefu_outcome


def test_entry_with_keywords_shows_up_to_two_examples():
    outcome = ServiceListOutcome(entries=(
        ServiceListEntry(label="库存内部调拨", keywords=("移库", "拆托", "并托", "重新打托")),
    ))
    reply = render_kefu_outcome(outcome)
    assert "库存内部调拨（如：移库、拆托）" in reply
    assert "并托" not in reply  # only the first two, keeps the list scannable


def test_entry_without_keywords_shows_bare_label():
    outcome = ServiceListOutcome(entries=(ServiceListEntry(label="服务说明"),))
    reply = render_kefu_outcome(outcome)
    assert reply == "当前可用服务：\n服务说明"


def test_multiple_entries_one_per_line():
    outcome = ServiceListOutcome(entries=(
        ServiceListEntry(label="U-Choice 入库申请", keywords=("入库", "收货")),
        ServiceListEntry(label="U-Choice 出库申请", keywords=("出库", "送货")),
    ))
    reply = render_kefu_outcome(outcome)
    assert reply == (
        "当前可用服务：\n"
        "U-Choice 入库申请（如：入库、收货）\n"
        "U-Choice 出库申请（如：出库、送货）"
    )
