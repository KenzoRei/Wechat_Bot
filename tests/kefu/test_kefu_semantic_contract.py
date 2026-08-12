"""
kefu-deterministic-response-plan.md Sec 3: structured AI contract offline
tests -- parsing, channel-mode prompt split, and validate_address_match's
untrusted-candidate-ID enforcement. No DB needed.
"""
from ai.base import AddressMatch, AIResponse, SemanticIssue
from ai.prompt_builder import build_system_prompt, parse_response
from core.kefu_response_renderer import validate_address_match

_CANDIDATES = [
    {"address_id": "addr-1", "company_name": "Acme", "addr": "1 Main St"},
    {"address_id": "addr-2", "company_name": "Beta", "addr": "2 Main St"},
]


def _base_context(**overrides):
    ctx = {
        "display_name": "Staff", "role": "admin", "allowed_services": [],
        "collected_fields": {}, "session_id": None, "session_status": None,
        "group_context": None, "uchoice_candidates": {},
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# Channel-mode prompt split
# ---------------------------------------------------------------------------

def test_smart_robot_prompt_unchanged_shape_keeps_legacy_pivot_instruction():
    ctx = _base_context(uchoice_candidates={"addresses": _CANDIDATES})
    prompt = build_system_prompt(ctx)
    assert "unmatched_new_address" in prompt
    assert "系统已将其转为新增地址流程，原出库申请已自动取消" in prompt
    assert "address_match" not in prompt


def test_kefu_prompt_uses_structured_contract_no_pivot_narration_instruction():
    ctx = _base_context(source_channel="kefu", uchoice_candidates={"addresses": _CANDIDATES})
    prompt = build_system_prompt(ctx)
    assert "address_match" in prompt
    assert "semantic_issues" in prompt
    # The instruction telling the AI to itself narrate that a pivot/
    # cancellation happened must be gone for Kefu -- only the backend, after
    # actually committing the mutation, may say that.
    assert "系统已将其转为新增地址流程，原出库申请已自动取消" not in prompt
    # The legacy field is only mentioned to explain it's no longer the
    # output target (see _address_matching_instructions), never asked for
    # in the Kefu response schema itself.
    assert '"unmatched_new_address": null' not in prompt
    assert "reply 字段不会发送给客服人员" in prompt


def test_kefu_prompt_with_no_candidates_omits_candidates_matching_rules():
    ctx = _base_context(source_channel="kefu", uchoice_candidates={})
    prompt = build_system_prompt(ctx)
    # The detailed candidate-matching rule block (SKU/address/etc. matching
    # instructions) is conditional on real candidates existing -- the bare
    # phrase "候选列表" also appears in unrelated, always-present example
    # text elsewhere in the prompt, so assert on content unique to the
    # matching-rules block instead.
    assert "必须将其与此列表的 description 语义匹配" not in prompt
    # The response schema itself (which always shows address_match/
    # semantic_issues for Kefu, candidates or not) is unaffected -- only the
    # per-candidate-type matching-rules text is conditional.
    assert '"address_match": null' in prompt


# ---------------------------------------------------------------------------
# parse_response: structured fields
# ---------------------------------------------------------------------------

def test_parse_response_extracts_address_match_matched():
    import json
    raw = json.dumps({
        "intent": "continuation", "reply": "", "extracted_fields": {},
        "all_fields_collected": False, "service_type_name": None,
        "address_match": {"status": "matched", "candidate_ids": ["addr-1"]},
    })
    resp = parse_response(raw)
    assert resp.address_match == AddressMatch(status="matched", candidate_ids=("addr-1",))


def test_parse_response_extracts_semantic_issues():
    import json
    raw = json.dumps({
        "intent": "continuation", "reply": "", "extracted_fields": {},
        "all_fields_collected": False, "service_type_name": None,
        "semantic_issues": [{"code": "unknown_value", "field": "sku_code", "value": "widget"}],
    })
    resp = parse_response(raw)
    assert len(resp.semantic_issues) == 1
    assert resp.semantic_issues[0] == SemanticIssue(code="unknown_value", field="sku_code", value="widget")


def test_parse_response_malformed_address_match_becomes_none_not_a_crash():
    import json
    raw = json.dumps({
        "intent": "continuation", "reply": "", "extracted_fields": {},
        "all_fields_collected": False, "service_type_name": None,
        "address_match": {"status": "not_a_real_status"},
    })
    resp = parse_response(raw)
    assert resp.address_match is None


def test_parse_response_malformed_semantic_issue_entry_is_dropped_not_a_crash():
    import json
    raw = json.dumps({
        "intent": "continuation", "reply": "", "extracted_fields": {},
        "all_fields_collected": False, "service_type_name": None,
        "semantic_issues": [{"code": "unknown_value"}, "not a dict", {"field": "x"}],
    })
    resp = parse_response(raw)
    assert resp.semantic_issues == ()


def test_parse_response_absent_structured_fields_default_empty():
    import json
    raw = json.dumps({
        "intent": "new_request", "reply": "hi", "extracted_fields": {},
        "all_fields_collected": True, "service_type_name": "view_storage",
    })
    resp = parse_response(raw)
    assert resp.semantic_issues == ()
    assert resp.address_match is None


# ---------------------------------------------------------------------------
# validate_address_match: untrusted-candidate-ID enforcement
# (kefu-deterministic-response-plan.md Sec 1 invariant 4)
# ---------------------------------------------------------------------------

def _resp(address_match):
    return AIResponse(
        intent="continuation", reply="", extracted_fields={},
        all_fields_collected=False, service_type_name=None, address_match=address_match,
    )


def test_matched_real_id_is_accepted():
    decision = validate_address_match(_resp(AddressMatch(status="matched", candidate_ids=("addr-1",))), _CANDIDATES)
    assert decision.status == "matched"
    assert decision.matched_address_id == "addr-1"


def test_matched_hallucinated_id_is_rejected_as_invalid():
    decision = validate_address_match(
        _resp(AddressMatch(status="matched", candidate_ids=("not-a-real-id",))), _CANDIDATES
    )
    assert decision.status == "invalid"
    assert decision.matched_address_id is None


def test_matched_with_two_ids_violates_invariant_and_is_rejected():
    decision = validate_address_match(
        _resp(AddressMatch(status="matched", candidate_ids=("addr-1", "addr-2"))), _CANDIDATES
    )
    assert decision.status == "invalid"


def test_ambiguous_two_real_ids_is_accepted():
    decision = validate_address_match(
        _resp(AddressMatch(status="ambiguous", candidate_ids=("addr-1", "addr-2"))), _CANDIDATES
    )
    assert decision.status == "ambiguous"
    assert set(decision.ambiguous_address_ids) == {"addr-1", "addr-2"}


def test_ambiguous_with_one_real_one_hallucinated_is_rejected():
    decision = validate_address_match(
        _resp(AddressMatch(status="ambiguous", candidate_ids=("addr-1", "fake-id"))), _CANDIDATES
    )
    assert decision.status == "invalid"


def test_ambiguous_two_real_plus_one_hallucinated_is_rejected_wholesale():
    """
    Codex round-119 finding: filtering out just the hallucinated ID and
    accepting the remaining two real ones is wrong -- a partially
    hallucinated list means the AI's output as a whole isn't trustworthy,
    not that the bad entry can be silently dropped.
    """
    decision = validate_address_match(
        _resp(AddressMatch(status="ambiguous", candidate_ids=("addr-1", "addr-2", "fake-id"))), _CANDIDATES
    )
    assert decision.status == "invalid"


def test_ambiguous_with_a_duplicated_real_id_is_rejected_not_two_options():
    """
    Codex round-119 finding: the same real ID reported twice is one
    candidate, not two -- it must not be accepted as genuine ambiguity.
    """
    decision = validate_address_match(
        _resp(AddressMatch(status="ambiguous", candidate_ids=("addr-1", "addr-1"))), _CANDIDATES
    )
    assert decision.status == "invalid"


def test_not_provided_status():
    decision = validate_address_match(_resp(AddressMatch(status="not_provided")), _CANDIDATES)
    assert decision.status == "not_provided"


def test_missing_address_match_entirely_is_not_provided():
    decision = validate_address_match(_resp(None), _CANDIDATES)
    assert decision.status == "not_provided"


def test_unmatched_sanitizes_new_address_to_known_keys_only():
    decision = validate_address_match(
        _resp(AddressMatch(status="unmatched", new_address={
            "company_name": "New Co", "addr": "9 New St", "customer_id": "should-not-survive", "note": "internal",
        })),
        _CANDIDATES,
    )
    assert decision.status == "unmatched"
    assert decision.unmatched_new_address == {"company_name": "New Co", "addr": "9 New St"}


def test_unmatched_with_no_new_address_data_is_none():
    decision = validate_address_match(_resp(AddressMatch(status="unmatched")), _CANDIDATES)
    assert decision.status == "unmatched"
    assert decision.unmatched_new_address is None


def test_unmatched_with_blank_strings_is_none():
    decision = validate_address_match(
        _resp(AddressMatch(status="unmatched", new_address={"company_name": "  ", "addr": ""})), _CANDIDATES
    )
    assert decision.unmatched_new_address is None
