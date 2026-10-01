"""
Kefu pending-request list layout and plain-text replies.

- The list is grouped by warehouse (【JFK 仓】), oldest first, one block per
  request: SKU lines, → destination, 创建：name · New York time.
- The stored numbered snapshot follows exactly the displayed order, so a
  reply like "1 3" picks what was shown next to those numbers.
- A destination's street address appears only when two listed requests share
  the destination name.
- Over Kefu's 2048-byte limit, the blocks are folded, never renumbered.
- WeCom Kefu sends plain text: markdown bold is converted (【标题】 / 「确认」).

Pure functions, no DB.
"""
from types import SimpleNamespace

from core import completion_batch
from core.kefu_outcomes import CandidateAmbiguousOutcome, CandidateOption
from core.kefu_response_renderer import KEFU_TEXT_MAX_BYTES, kefu_plain_text, render_kefu_outcome
from core.kefu_turn_apply import _display_time, _resolve_reference_serial


def _candidate(serial, warehouse, created_at, dest="旧Longo", addr="2179-20 149th Ave, Jamaica, NY 11434",
               creator="Simon", skus=("T1 3-inch Clear Packing Tape ×1托",)):
    return {
        "serial_number": serial, "warehouse_code": warehouse, "created_at": created_at,
        "sku_summary": "x", "sku_lines_display": list(skus),
        "destination": f"{dest}（{addr}）", "destination_name": dest, "destination_address": addr,
        "created_by_name": creator,
    }


CANDIDATES = [
    _candidate("REQ-20260930-000122", "JFK", "2026-09-30T14:12:00+00:00", dest="JFK仓库自提留存", creator="Lily",
               skus=("S2 1500 ft Stretch Wrap ×1箱（散）", "T2 3-inch Dark Brown Packing Tape ×1箱（散）")),
    _candidate("REQ-20260929-000114", "NJ", "2026-09-29T13:30:00+00:00", dest="散客", addr=""),
    _candidate("REQ-20260928-000108", "JFK", "2026-09-28T18:05:00+00:00", dest="旧Fast Track"),
    _candidate("REQ-20260928-000109", "JFK", "2026-09-28T20:40:00+00:00"),
]


def _listing(candidates=CANDIDATES, service="confirm_outbound_completion"):
    session = SimpleNamespace(collected_fields={})
    context = {"uchoice_candidates": {"pending_outbound_requests": candidates},
               "allowed_services": [{"name": "confirm_outbound_completion_batch"}]}
    reply, keep_open = _resolve_reference_serial(context, session, {"name": service})
    return reply, session.collected_fields["_candidate_snapshot"]


def test_grouped_by_warehouse_oldest_first_with_snapshot_in_display_order():
    reply, snapshot = _listing()
    assert snapshot == ["REQ-20260928-000108", "REQ-20260928-000109", "REQ-20260930-000122", "REQ-20260929-000114"]
    lines = reply.splitlines()
    assert lines[0] == "当前有 4 笔待处理的出库申请，请问是哪一条？"
    assert lines.index("【JFK 仓】") < lines.index("1. REQ-20260928-000108") < lines.index("【NJ 仓】")
    assert lines.index("【NJ 仓】") < lines.index("4. REQ-20260929-000114")
    # numbers mean what is shown next to them
    for i, serial in enumerate(snapshot, start=1):
        assert f"{i}. {serial}" in lines
    # the fallback used when no list was shown agrees with the display
    assert completion_batch.snapshot_serials(CANDIDATES) == snapshot


def test_request_block_lines():
    reply, _ = _listing()
    block = reply.split("3. REQ-20260930-000122\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert block == [
        "   S2 1500 ft Stretch Wrap ×1箱（散）",
        "   T2 3-inch Dark Brown Packing Tape ×1箱（散）",
        "   → JFK仓库自提留存",
        "   创建：Lily · 9/30 10:12",          # 14:12 UTC = 10:12 New York (EDT)
    ]
    assert "→ 散客" in reply and "散客（" not in reply   # no empty brackets


def test_address_only_when_destination_names_collide():
    reply, _ = _listing()
    assert "→ 旧Longo\n" in reply + "\n" and "149th Ave" not in reply
    twin = _candidate("REQ-20260928-000110", "JFK", "2026-09-28T21:00:00+00:00", addr="99 Other St, Queens, NY")
    reply, _ = _listing(CANDIDATES + [twin])
    assert "→ 旧Longo（2179-20 149th Ave, Jamaica, NY 11434）" in reply
    assert "→ 旧Longo（99 Other St, Queens, NY）" in reply


def test_missing_creator_or_time_is_left_out_never_an_id():
    bare = [dict(c, created_by_name=None) for c in CANDIDATES[:2]]
    reply, _ = _listing(bare)
    assert "创建：9/30 10:12" in reply and "None" not in reply


def test_display_time_is_new_york():
    assert _display_time("2026-01-15T17:05:00+00:00") == "1/15 12:05"   # EST
    assert _display_time("2026-07-15T17:05:00+00:00") == "7/15 13:05"   # EDT
    assert _display_time(None) is None and _display_time("garbage") is None


def test_footer_example_is_unambiguous():
    reply, _ = _listing()
    assert reply.endswith("\n\n回复编号确认，可多选（如「1 3」或「全部」）。")
    assert completion_batch.parse_partial_reply("1 3", 4) == [1, 3]


def test_long_list_folds_details_but_keeps_numbers():
    long_skus = tuple(f"SKU {i} {'very long product name ' * 6}×1托" for i in range(6))
    options = tuple(
        CandidateOption(candidate_key=f"REQ-{i}", label=f"REQ-{i}", group="JFK 仓", details=long_skus)
        for i in range(1, 10)
    )
    text = render_kefu_outcome(CandidateAmbiguousOutcome(prompt="请问是哪一条？", options=options, footer=("回复编号",)))
    assert len(text.encode("utf-8")) <= KEFU_TEXT_MAX_BYTES
    for i in range(1, 10):
        assert f"{i}. REQ-{i}" in text


def test_plain_options_render_as_before():
    options = (CandidateOption("a", "选项一"), CandidateOption("b", "选项二"))
    assert render_kefu_outcome(CandidateAmbiguousOutcome(prompt="哪一个？", options=options)) == "哪一个？\n1. 选项一\n2. 选项二"


def test_kefu_plain_text():
    assert kefu_plain_text("**请确认以下信息**\n- 仓库：JFK") == "【请确认以下信息】\n- 仓库：JFK"
    assert kefu_plain_text("回复 **确认** 提交申请，或 **取消** 放弃。") == "回复「确认」提交申请，或「取消」放弃。"
    assert kefu_plain_text("\n**各仓合计**\n- DE：$10") == "\n【各仓合计】\n- DE：$10"
    assert kefu_plain_text("stray ** here") == "stray  here"
    assert kefu_plain_text("no markdown") == "no markdown"
    assert kefu_plain_text(None) is None
    once = kefu_plain_text("**标题**\n回复 **确认**")
    assert kefu_plain_text(once) == once


def test_confirmation_and_batch_templates_reach_kefu_without_asterisks():
    from core.confirmation import BATCH_CONFIRMATION_FOOTER, _DEFAULT_CONFIRMATION_FOOTER
    for template in (_DEFAULT_CONFIRMATION_FOOTER, BATCH_CONFIRMATION_FOOTER, "**请确认以下信息**"):
        assert "**" not in kefu_plain_text(template)
