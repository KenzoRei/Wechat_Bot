"""
Batch cancellation, offline (docs/ai-collaboration/2026-10-cancel-batch/plan.md):
the shared family table, per-action wording, the prompt contract, and the
Smart Robot exclusion. No database.
"""
import pytest

from core import completion_batch as cb


def test_family_table_is_consistent():
    for fam in cb.FAMILIES:
        assert cb.family(fam.single) is fam and cb.family(fam.batch) is fam
        assert cb.BATCH_SERVICE_BY_SINGLE[fam.single] == fam.batch
        assert cb.SINGLE_SERVICE_BY_BATCH[fam.batch] == fam.single
        assert cb.DIRECTION_BY_SERVICE[fam.single] == cb.DIRECTION_BY_SERVICE[fam.batch] == fam.direction
        assert cb.is_batch_service(fam.batch) and not cb.is_batch_service(fam.single)
    assert len({(f.action, f.direction) for f in cb.FAMILIES}) == len(cb.FAMILIES)


def test_completion_views_are_unchanged():
    assert cb.BATCH_SERVICE_BY_SINGLE["confirm_outbound_completion"] == "confirm_outbound_completion_batch"
    assert cb.family("confirm_inbound_completion").candidate_key == "pending_inbound_requests"
    assert cb.family("cancel_outbound_request").candidate_key == "cancelable_outbound_requests"


def test_confirm_and_cancel_of_one_direction_are_different_families():
    from core.kefu_case_adapter import _same_completion_family
    assert _same_completion_family("cancel_outbound_request", "cancel_outbound_request_batch")
    assert _same_completion_family("confirm_outbound_completion", "confirm_outbound_completion_batch")
    assert not _same_completion_family("confirm_outbound_completion", "cancel_outbound_request")
    assert not _same_completion_family("cancel_inbound_request", "cancel_outbound_request_batch")


@pytest.mark.parametrize("reply, action, selects_all", [
    ("全部取消", "cancel", True), ("都取消", "cancel", True), ("全部", "cancel", True),
    ("全部取消", "complete", False), ("全部确认", "complete", True), ("全部确认", "cancel", False),
])
def test_select_all_replies_depend_on_the_action(reply, action, selects_all):
    assert (reply in cb.SELECT_ALL_REPLIES_BY_ACTION[action]) is selects_all


def test_resolve_selection_notes_use_the_action_verb():
    _, notes = cb.resolve_selection({"serials": ["REQ-X"]}, ["REQ-1"], ["REQ-1"], "取消")
    assert notes == ["REQ-X 不在可取消的待处理申请中"]


def test_cancel_footer_never_offers_qu_xiao_as_abandon():
    from core.confirmation import batch_confirmation_footer
    footer = batch_confirmation_footer("cancel")
    assert "取消后无法恢复" in footer and "**放弃**" in footer and "**取消** 放弃" not in footer
    assert batch_confirmation_footer("complete") == "回复 **确认** 提交全部，**取消** 放弃，或 部分确认（请回复编号，如：①③）。"


def _prompt(service_names):
    from ai.prompt_builder import build_system_prompt
    return build_system_prompt({
        "display_name": "Staff", "role": "customer", "collected_fields": {}, "session_id": None,
        "session_status": None, "group_context": None, "source_channel": "kefu",
        "allowed_services": [{"name": n, "description": "", "input_schema": {}} for n in service_names],
        "uchoice_candidates": {"cancelable_outbound_requests": [{"serial_number": "REQ-1"}]},
    })


def test_prompt_has_cancel_batch_rules_only_when_granted():
    with_batch = _prompt(["cancel_outbound_request", "cancel_outbound_request_batch"])
    assert "【批量取消】" in with_batch and '{"select_all": true}' in with_batch
    assert "【批量确认】" not in with_batch          # format spelled out without the completion section
    assert "【批量取消】" not in _prompt(["cancel_outbound_request"])


def test_smart_robot_never_offered_batch_cancel():
    from core.access_control import KEFU_ONLY_SERVICE_NAMES
    assert {"cancel_inbound_request_batch", "cancel_outbound_request_batch"} <= KEFU_ONLY_SERVICE_NAMES
