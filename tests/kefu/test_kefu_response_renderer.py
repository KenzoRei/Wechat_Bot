"""
kefu-deterministic-response-plan.md Sec 4: renderer exhaustiveness, no
internal-ID/exception-text leakage, and payload validation. No DB needed --
core/kefu_response_renderer.py is a pure function of pre-resolved facts.
"""
import typing

import pytest

import core.kefu_outcomes as oc
from core.kefu_response_renderer import render_kefu_outcome, _RENDERERS


# ---------------------------------------------------------------------------
# Exhaustiveness
# ---------------------------------------------------------------------------

def test_every_outcome_type_in_the_closed_union_has_a_registered_renderer():
    union_members = typing.get_args(oc.KefuOutcome)
    assert len(union_members) >= 30, "sanity check: the union should be large, not accidentally empty"
    missing = [t.__name__ for t in union_members if t not in _RENDERERS]
    assert missing == [], f"outcome types with no renderer: {missing}"


def test_unregistered_type_raises_not_a_silent_fallback():
    class NotARealOutcome:
        pass

    with pytest.raises(NotImplementedError):
        render_kefu_outcome(NotARealOutcome())


# ---------------------------------------------------------------------------
# One representative instance per outcome family renders non-empty text
# without touching a database or raising
# ---------------------------------------------------------------------------

_SAMPLES = [
    oc.MissingFieldsOutcome(
        service_label="出库申请",
        fields=(oc.FieldPrompt(field="warehouse_code", label="仓库", question="请问从哪个仓库出库？"),),
    ),
    oc.InvalidValueOutcome(field_label="重量", reason="必须为正数"),
    oc.UnknownCatalogValueOutcome(field_label="商品", raw_value="不存在的商品"),
    oc.ContradictoryValueOutcome(field_label="仓库", detail="与已收集信息冲突"),
    oc.FieldCorrectionAcceptedOutcome(field_label="仓库", new_value_label="DE"),
    oc.CandidateAmbiguousOutcome(
        prompt="请确认是哪一条：",
        options=(oc.CandidateOption(candidate_key="REQ-A", label="REQ-A（JFK仓）"),
                  oc.CandidateOption(candidate_key="REQ-B", label="REQ-B（DE仓）")),
    ),
    oc.CandidateNoneEligibleOutcome(explanation="当前没有待处理的申请。"),
    oc.AddressAmbiguousOutcome(options=(
        oc.AddressOption(candidate_key="POISON-UUID-1", display_label="ABC 公司（1 Main St）"),
        oc.AddressOption(candidate_key="POISON-UUID-2", display_label="XYZ 公司（2 Main St）"),
    )),
    oc.AddressPivotUnavailableOutcome(escalation_note="您没有新增地址的权限，请联系管理员。"),
    oc.AddressPivotStartedOutcome(cancelled_serial_number="REQ-1", still_missing_fields=(
        oc.FieldPrompt(field="charge_type", label="计费类型", question="请问计费类型是？"),
    )),
    oc.InsufficientStockOutcome(
        warehouse_label="JFK",
        shortages=(oc.StockShortage(sku_label="S2", requested_boxes=144, available_boxes=100),),
    ),
    oc.StockChangedOutcome(
        serial_number="REQ-2",
        shortages=(oc.StockShortage(sku_label="S2", requested_boxes=144, available_boxes=100),),
    ),
    oc.InventoryInconsistentOutcome(note="库存记录异常"),
    oc.ConfirmationSummaryOutcome(summary_text="请确认以下信息：..."),
    oc.ConfirmationCancelledOutcome(service_label="出库申请"),
    oc.ConfirmationAlreadyProcessedOutcome(),
    oc.ConfirmationNothingPendingOutcome(),
    oc.ConfirmationRecoveringOutcome(),
    oc.SessionConflictOutcome(service_label="出库申请", case_number="CASE-1", last_question="请提供目的地地址。"),
    oc.ExecutionSubmittedOutcome(serial_number="REQ-3", service_label="入库申请"),
    oc.ExecutionCompletedOutcome(serial_number="REQ-4", service_label="出库申请", result_lines=("已扣减库存",)),
    oc.ExecutionRetryableFailureOutcome(service_label="出库申请"),
    oc.ExecutionPermanentFailureOutcome(service_label="出库申请", reason="数据异常"),
    oc.PermissionDeniedOutcome(reason_label="仅限管理员操作"),
    oc.NoCaseOutcome(),
    oc.CaseClosedOutcome(serial_number="REQ-5"),
    oc.CaseStaleOutcome(serial_number="REQ-6"),
    oc.ServiceListOutcome(entries=(
        oc.ServiceListEntry(label="查库存", keywords=("查库存", "库存查询")),
        oc.ServiceListEntry(label="入库申请"),
    )),
    oc.UnrecognizedRequestOutcome(),
    oc.ServiceUnavailableOutcome(),
    oc.QueryEmptyOutcome(query_label="库存查询"),
    oc.QueryResultOutcome(title="库存查询结果", body_text="JFK: S2 x10托"),
    oc.QueryInvalidFiltersOutcome(reason="起止月份缺失"),
    oc.CompletionNoticeOutcome(serial_number="REQ-7", direction_label="入库"),
    oc.SemanticUnavailableOutcome(),
]


@pytest.mark.parametrize("outcome", _SAMPLES, ids=lambda o: type(o).__name__)
def test_renders_non_empty_text_without_raising(outcome):
    text = render_kefu_outcome(outcome)
    assert isinstance(text, str)
    assert text.strip()


def test_samples_cover_every_outcome_type_in_the_union():
    covered = {type(o) for o in _SAMPLES}
    union_members = set(typing.get_args(oc.KefuOutcome))
    assert covered == union_members, f"sample list is out of sync with the union: missing {union_members - covered}"


def test_address_ambiguous_render_never_leaks_the_raw_candidate_key():
    """
    candidate_key on AddressOption is an opaque per-turn token (a real
    address_id in production) -- the renderer must only ever surface
    display_label, never candidate_key itself, so an internal ID can never
    reach a customer-facing or staff-facing message this way.
    """
    outcome = oc.AddressAmbiguousOutcome(options=(
        oc.AddressOption(candidate_key="11111111-aaaa-bbbb-cccc-222222222222", display_label="ABC 公司（1 Main St）"),
        oc.AddressOption(candidate_key="33333333-dddd-eeee-ffff-444444444444", display_label="XYZ 公司（2 Main St）"),
    ))
    text = render_kefu_outcome(outcome)
    assert "11111111-aaaa-bbbb-cccc-222222222222" not in text
    assert "33333333-dddd-eeee-ffff-444444444444" not in text
    assert "ABC 公司" in text and "XYZ 公司" in text


def test_candidate_ambiguous_render_never_leaks_the_raw_candidate_key():
    outcome = oc.CandidateAmbiguousOutcome(
        prompt="请选择：",
        options=(oc.CandidateOption(candidate_key="internal-key-1", label="REQ-A（JFK仓）"),
                  oc.CandidateOption(candidate_key="internal-key-2", label="REQ-B（DE仓）")),
    )
    text = render_kefu_outcome(outcome)
    assert "internal-key-1" not in text
    assert "internal-key-2" not in text


# ---------------------------------------------------------------------------
# Payload validation: missing/empty/contradictory facts rejected at
# construction, not at render time
# ---------------------------------------------------------------------------

def test_missing_fields_outcome_rejects_empty_fields_tuple():
    with pytest.raises(oc.KefuOutcomeError):
        oc.MissingFieldsOutcome(service_label="入库申请", fields=())


def test_missing_fields_outcome_rejects_empty_service_label():
    with pytest.raises(oc.KefuOutcomeError):
        oc.MissingFieldsOutcome(
            service_label="",
            fields=(oc.FieldPrompt(field="x", label="X", question="?"),),
        )


def test_field_prompt_rejects_empty_question():
    with pytest.raises(oc.KefuOutcomeError):
        oc.FieldPrompt(field="x", label="X", question="")


def test_stock_shortage_rejects_a_non_shortage_where_available_covers_requested():
    with pytest.raises(oc.KefuOutcomeError):
        oc.StockShortage(sku_label="S2", requested_boxes=10, available_boxes=20)


def test_stock_shortage_rejects_zero_requested():
    with pytest.raises(oc.KefuOutcomeError):
        oc.StockShortage(sku_label="S2", requested_boxes=0, available_boxes=0)


def test_stock_shortage_rejects_negative_available():
    with pytest.raises(oc.KefuOutcomeError):
        oc.StockShortage(sku_label="S2", requested_boxes=10, available_boxes=-1)


def test_insufficient_stock_outcome_rejects_empty_shortages():
    with pytest.raises(oc.KefuOutcomeError):
        oc.InsufficientStockOutcome(warehouse_label="JFK", shortages=())


def test_address_ambiguous_outcome_rejects_fewer_than_two_options():
    with pytest.raises(oc.KefuOutcomeError):
        oc.AddressAmbiguousOutcome(options=(oc.AddressOption(candidate_key="a", display_label="A"),))


def test_candidate_ambiguous_outcome_rejects_fewer_than_two_options():
    with pytest.raises(oc.KefuOutcomeError):
        oc.CandidateAmbiguousOutcome(
            prompt="?", options=(oc.CandidateOption(candidate_key="a", label="A"),)
        )


def test_completion_notice_outcome_rejects_a_non_inbound_outbound_direction_label():
    """This is exactly the round-102-incident bug class this phase fixes --
    a generic/unknown direction label must never become a renderable
    completion notice."""
    with pytest.raises(oc.KefuOutcomeError):
        oc.CompletionNoticeOutcome(serial_number="REQ-1", direction_label="请求")


def test_completion_notice_outcome_accepts_only_the_two_real_directions():
    oc.CompletionNoticeOutcome(serial_number="REQ-1", direction_label="入库")
    oc.CompletionNoticeOutcome(serial_number="REQ-1", direction_label="出库")


def test_missing_required_dataclass_field_raises_type_error():
    with pytest.raises(TypeError):
        oc.ExecutionSubmittedOutcome(serial_number="REQ-1")  # missing service_label
