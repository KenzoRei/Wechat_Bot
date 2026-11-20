"""The LLM prompt explains the two pickup types (plan D4, D10, D13)."""
import pytest

from ai.prompt_builder import build_system_prompt


def _prompt(service_names, channel):
    return build_system_prompt({
        "display_name": "Staff", "role": "customer", "collected_fields": {}, "session_id": None,
        "session_status": None, "group_context": None, "source_channel": channel,
        "allowed_services": [{"name": n, "description": "", "input_schema": {}} for n in service_names],
        "uchoice_candidates": {"addresses": []},
    })


@pytest.mark.parametrize("channel", ["kefu", "smart_robot"])
def test_both_pickup_types_are_explained_on_both_channels(channel):
    prompt = _prompt(["uchoice_outbound_request"], channel)
    assert "pickup_type" in prompt and "pickup_customer_name" in prompt
    assert "客户自提" in prompt and "仓库自提留存" in prompt


@pytest.mark.parametrize("channel", ["kefu", "smart_robot"])
def test_retention_means_the_recipient_is_us_and_stock_is_deducted(channel):
    prompt = _prompt(["uchoice_outbound_request"], channel)
    assert "Trans World" in prompt and "库存仍照常扣减" in prompt


def test_intent_not_the_literal_word_decides_pickup():
    prompt = _prompt(["uchoice_outbound_request"], "kefu")
    for phrase in ("自取", "来提货", "到仓提货", "pickup", "will call"):
        assert phrase in prompt
    assert "不要只看有没有" in prompt


def test_a_bare_ziti_asks_which_type_instead_of_choosing():
    prompt = _prompt(["uchoice_outbound_request"], "kefu")
    assert "不要自己选" in prompt and "请告知取货人姓名" in prompt


def test_pickup_wins_over_a_named_address_and_the_ai_never_writes_the_pickup_row():
    prompt = _prompt(["uchoice_outbound_request"], "smart_robot")
    assert "以自提为准" in prompt
    assert "绝不能自己填 destination_address_id 来表示自提" in prompt


def test_revisions_are_re_stated_in_both_directions():
    prompt = _prompt(["uchoice_outbound_request"], "kefu")
    assert "这不是送货，是客户自己来提货" in prompt and "不是自提，送到 X" in prompt


def test_walk_in_is_a_customer_pickup_named_sanke():
    assert "散客就填" in _prompt(["uchoice_outbound_request"], "kefu")


def test_callers_who_cannot_submit_outbound_requests_do_not_see_the_pickup_rules():
    prompt = _prompt(["uchoice_inbound_request"], "kefu")
    assert "pickup_type" not in prompt
