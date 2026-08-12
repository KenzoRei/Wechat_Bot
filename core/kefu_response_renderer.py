"""
The only operational-text renderer for Kefu (kefu-deterministic-response-plan
.md Sec 4/5). Pure functions of already-validated, already-labeled facts --
this module never queries or mutates the database (plan Sec 12's integration
contract: Codex's orchestration resolves labels via existing db-touching
helpers first, then constructs a KefuOutcome; this module only formats it).

Two published entry points, per plan Sec 12:
    validate_address_match(ai_response, candidates) -> AddressDecision
    render_kefu_outcome(outcome: KefuOutcome) -> str
"""
from __future__ import annotations

from dataclasses import dataclass

from core.kefu_outcomes import (
    AddressAmbiguousOutcome,
    AddressPivotStartedOutcome,
    AddressPivotUnavailableOutcome,
    CandidateAmbiguousOutcome,
    CandidateNoneEligibleOutcome,
    CaseClosedOutcome,
    CaseStaleOutcome,
    CompletionNoticeOutcome,
    ConfirmationAlreadyProcessedOutcome,
    ConfirmationCancelledOutcome,
    ConfirmationNothingPendingOutcome,
    ConfirmationRecoveringOutcome,
    ConfirmationSummaryOutcome,
    SessionConflictOutcome,
    ContradictoryValueOutcome,
    ExecutionCompletedOutcome,
    ExecutionPermanentFailureOutcome,
    ExecutionRetryableFailureOutcome,
    ExecutionSubmittedOutcome,
    FieldCorrectionAcceptedOutcome,
    InsufficientStockOutcome,
    InventoryInconsistentOutcome,
    InvalidValueOutcome,
    KefuOutcome,
    MissingFieldsOutcome,
    NoCaseOutcome,
    PermissionDeniedOutcome,
    QueryEmptyOutcome,
    QueryInvalidFiltersOutcome,
    QueryResultOutcome,
    SemanticUnavailableOutcome,
    ServiceListEntry,
    ServiceListOutcome,
    ServiceUnavailableOutcome,
    StockChangedOutcome,
    UnknownCatalogValueOutcome,
    UnrecognizedRequestOutcome,
)


# ---------------------------------------------------------------------------
# validate_address_match
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AddressDecision:
    """
    The backend's OWN decision about an AI-reported address match, after
    validating every candidate ID against the exact list supplied for this
    turn (plan Sec 1 invariant 4: AI candidate IDs are untrusted until
    validated). `status` is one of:
      "matched"      -- exactly one candidate ID, and it's real.
      "ambiguous"     -- two or more candidate IDs, all real.
      "unmatched"     -- the AI reported no match; sanitized company_name/
                          addr text (if any) is retained for a possible pivot.
      "not_provided"  -- the AI reported the customer hasn't stated a
                          destination yet, or gave no address_match at all.
      "invalid"       -- the AI's address_match named an ID that is NOT in
                          the real candidate set (hallucinated), or a status
                          whose candidate-count invariant it violated
                          (e.g. "matched" with zero or two+ IDs). Never
                          persisted; orchestration should treat this the same
                          as "not_provided" for user-facing purposes but may
                          log it distinctly.
    """
    status: str
    matched_address_id: str | None = None
    ambiguous_address_ids: tuple[str, ...] = ()
    unmatched_new_address: dict | None = None


def _sanitize_new_address(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    sanitized = {}
    for key in ("company_name", "addr"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            sanitized[key] = value.strip()
    return sanitized or None


def validate_address_match(ai_response, candidates: list[dict]) -> AddressDecision:
    """
    `candidates` is the exact turn-local address candidate list (the same
    list injected into the AI's prompt this turn, e.g.
    context["uchoice_candidates"]["addresses"]) -- never a broader or later
    query, so a candidate ID is only ever accepted if it was actually offered
    to the AI on THIS turn.
    """
    valid_ids = {c["address_id"] for c in candidates}
    match = getattr(ai_response, "address_match", None)

    if match is None or match.status == "not_provided":
        return AddressDecision(status="not_provided")

    if match.status == "matched":
        if len(match.candidate_ids) == 1 and match.candidate_ids[0] in valid_ids:
            return AddressDecision(status="matched", matched_address_id=match.candidate_ids[0])
        return AddressDecision(status="invalid")

    if match.status == "ambiguous":
        # Codex round-119 finding: every reported ID must be turn-local (not
        # just "at least two of them are"), and they must be distinct -- a
        # partially hallucinated list, or a real ID repeated to reach a count
        # of two, is not genuine ambiguity between two options. Order is
        # preserved (a plain set would scramble display order); "seen"
        # de-duplicates without relying on set iteration order.
        seen: list[str] = []
        for cid in match.candidate_ids:
            if cid not in seen:
                seen.append(cid)
        if len(seen) >= 2 and all(cid in valid_ids for cid in seen):
            return AddressDecision(status="ambiguous", ambiguous_address_ids=tuple(seen))
        return AddressDecision(status="invalid")

    if match.status == "unmatched":
        return AddressDecision(status="unmatched", unmatched_new_address=_sanitize_new_address(match.new_address))

    return AddressDecision(status="invalid")


# ---------------------------------------------------------------------------
# render_kefu_outcome
# ---------------------------------------------------------------------------

def _render_missing_fields(o: MissingFieldsOutcome) -> str:
    questions = "\n".join(f"- {f.question}" for f in o.fields)
    return f"办理{o.service_label}还需要以下信息：\n{questions}"


def _render_invalid_value(o: InvalidValueOutcome) -> str:
    return f"{o.field_label}的内容有误：{o.reason}，请重新提供。"


def _render_unknown_catalog_value(o: UnknownCatalogValueOutcome) -> str:
    return f"未能识别{o.field_label}“{o.raw_value}”，请换一种描述或直接提供准确信息。"


def _render_contradictory_value(o: ContradictoryValueOutcome) -> str:
    return f"{o.field_label}的信息前后矛盾：{o.detail}，请确认后重新提供。"


def _render_field_correction_accepted(o: FieldCorrectionAcceptedOutcome) -> str:
    return f"已更新{o.field_label}为{o.new_value_label}。"


def _render_candidate_ambiguous(o: CandidateAmbiguousOutcome) -> str:
    lines = "\n".join(f"{i}. {opt.label}" for i, opt in enumerate(o.options, start=1))
    return f"{o.prompt}\n{lines}"


def _render_candidate_none_eligible(o: CandidateNoneEligibleOutcome) -> str:
    return o.explanation


def _render_address_ambiguous(o: AddressAmbiguousOutcome) -> str:
    lines = "\n".join(f"{i}. {opt.display_label}" for i, opt in enumerate(o.options, start=1))
    return f"目的地可能对应以下多项，请确认是哪一个：\n{lines}"


def _render_address_pivot_unavailable(o: AddressPivotUnavailableOutcome) -> str:
    return o.escalation_note


def _render_address_pivot_started(o: AddressPivotStartedOutcome) -> str:
    base = (
        f"目的地不在已收录的地址列表中，原申请 {o.cancelled_serial_number} 已自动取消，"
        "系统已为您转入新增地址流程。"
    )
    if o.still_missing_fields:
        questions = "\n".join(f"- {f.question}" for f in o.still_missing_fields)
        base += f"\n还需要补充以下信息：\n{questions}"
    base += "\n新增完成后请重新提交出库申请。"
    return base


def _render_insufficient_stock(o: InsufficientStockOutcome) -> str:
    lines = "\n".join(
        f"- {s.sku_label}：申请 {s.requested_boxes} 箱，{o.warehouse_label}仓现有 {s.available_boxes} 箱"
        for s in o.shortages
    )
    return f"申请已取消：{o.warehouse_label} 仓库库存不足——\n{lines}\n请核实商品规格或数量后重新提交。"


def _render_stock_changed(o: StockChangedOutcome) -> str:
    lines = "\n".join(
        f"- {s.sku_label}：需要 {s.requested_boxes} 箱，实际可用 {s.available_boxes} 箱"
        for s in o.shortages
    )
    return (
        f"申请 {o.serial_number} 的库存自提交后发生变化，本次确认未执行任何扣减——\n{lines}\n"
        "请重新核实实际发货情况后再次确认。"
    )


def _render_inventory_inconsistent(o: InventoryInconsistentOutcome) -> str:
    return f"库存状态异常：{o.note}，请联系管理员核实后重试。"


def _render_confirmation_summary(o: ConfirmationSummaryOutcome) -> str:
    return o.summary_text


def _render_confirmation_cancelled(o: ConfirmationCancelledOutcome) -> str:
    if o.serial_number:
        return f"{o.service_label}已取消（{o.serial_number}），您可以随时发起新申请。"
    return f"{o.service_label}已取消，您可以随时发起新申请。"


def _render_confirmation_already_processed(_: ConfirmationAlreadyProcessedOutcome) -> str:
    return "该申请已处理或已关闭，不能重复确认。"


def _render_confirmation_nothing_pending(_: ConfirmationNothingPendingOutcome) -> str:
    return "抱歉，未找到待确认的申请，请重新发起。"


def _render_confirmation_recovering(_: ConfirmationRecoveringOutcome) -> str:
    return "该申请已经提交，正在恢复消息投递，请勿重复确认。"


def _render_session_conflict(o: SessionConflictOutcome) -> str:
    lines = [f"您有一个正在进行的{o.service_label}：{o.case_number}。"]
    if o.last_question:
        lines.append(f"上次系统询问：「{o.last_question}」")
    lines.append('请回复 **取消** 结束该申请并处理新请求，或回复 **继续** 继续完成该申请。')
    return "\n".join(lines)


def _render_execution_submitted(o: ExecutionSubmittedOutcome) -> str:
    return f"{o.service_label}已提交，申请编号 {o.serial_number}，等待仓库处理。"


def _render_execution_completed(o: ExecutionCompletedOutcome) -> str:
    base = f"{o.service_label}已完成，申请编号 {o.serial_number}。"
    if o.result_lines:
        base += "\n" + "\n".join(o.result_lines)
    return base


def _render_execution_retryable_failure(o: ExecutionRetryableFailureOutcome) -> str:
    return f"{o.service_label}暂时无法处理，请稍后重试。"


def _render_execution_permanent_failure(o: ExecutionPermanentFailureOutcome) -> str:
    return f"{o.service_label}处理失败：{o.reason}，请联系管理员。"


def _render_permission_denied(o: PermissionDeniedOutcome) -> str:
    return f"您没有权限执行此操作：{o.reason_label}。"


def _render_no_case(_: NoCaseOutcome) -> str:
    return "未找到该案件，请核对案件编号。"


def _render_case_closed(o: CaseClosedOutcome) -> str:
    return f"案件 {o.serial_number} 已结束，无法继续操作。"


def _render_case_stale(o: CaseStaleOutcome) -> str:
    return f"案件 {o.serial_number} 已被其他操作更新，请重新核对当前状态后再试。"


def _render_service_list(o: ServiceListOutcome) -> str:
    def line(entry: ServiceListEntry) -> str:
        if not entry.keywords:
            return entry.label
        example = "、".join(entry.keywords[:2])
        return f"{entry.label}（如：{example}）"
    return "当前可用服务：\n" + "\n".join(line(e) for e in o.entries)


def _render_unrecognized_request(_: UnrecognizedRequestOutcome) -> str:
    return "抱歉，没能理解您需要哪项服务，请重新描述一下您的需求。"


def _render_service_unavailable(_: ServiceUnavailableOutcome) -> str:
    return "该服务暂未在企业微信客服开放，请通过其他渠道办理，或联系管理员。"


def _render_query_empty(o: QueryEmptyOutcome) -> str:
    return f"{o.query_label}：暂无数据。"


def _render_query_result(o: QueryResultOutcome) -> str:
    return f"{o.title}\n{o.body_text}"


def _render_query_invalid_filters(o: QueryInvalidFiltersOutcome) -> str:
    return f"查询条件有误：{o.reason}，请重新提供。"


def _render_completion_notice(o: CompletionNoticeOutcome) -> str:
    return f"提示：申请 {o.serial_number}（{o.direction_label}）已由仓库确认完成。"


def _render_semantic_unavailable(_: SemanticUnavailableOutcome) -> str:
    return "抱歉，系统暂时无法处理您的请求，请稍后重试。"


_RENDERERS = {
    MissingFieldsOutcome: _render_missing_fields,
    InvalidValueOutcome: _render_invalid_value,
    UnknownCatalogValueOutcome: _render_unknown_catalog_value,
    ContradictoryValueOutcome: _render_contradictory_value,
    FieldCorrectionAcceptedOutcome: _render_field_correction_accepted,
    CandidateAmbiguousOutcome: _render_candidate_ambiguous,
    CandidateNoneEligibleOutcome: _render_candidate_none_eligible,
    AddressAmbiguousOutcome: _render_address_ambiguous,
    AddressPivotUnavailableOutcome: _render_address_pivot_unavailable,
    AddressPivotStartedOutcome: _render_address_pivot_started,
    InsufficientStockOutcome: _render_insufficient_stock,
    StockChangedOutcome: _render_stock_changed,
    InventoryInconsistentOutcome: _render_inventory_inconsistent,
    ConfirmationSummaryOutcome: _render_confirmation_summary,
    ConfirmationCancelledOutcome: _render_confirmation_cancelled,
    ConfirmationAlreadyProcessedOutcome: _render_confirmation_already_processed,
    ConfirmationNothingPendingOutcome: _render_confirmation_nothing_pending,
    ConfirmationRecoveringOutcome: _render_confirmation_recovering,
    SessionConflictOutcome: _render_session_conflict,
    ExecutionSubmittedOutcome: _render_execution_submitted,
    ExecutionCompletedOutcome: _render_execution_completed,
    ExecutionRetryableFailureOutcome: _render_execution_retryable_failure,
    ExecutionPermanentFailureOutcome: _render_execution_permanent_failure,
    PermissionDeniedOutcome: _render_permission_denied,
    NoCaseOutcome: _render_no_case,
    CaseClosedOutcome: _render_case_closed,
    CaseStaleOutcome: _render_case_stale,
    ServiceListOutcome: _render_service_list,
    UnrecognizedRequestOutcome: _render_unrecognized_request,
    ServiceUnavailableOutcome: _render_service_unavailable,
    QueryEmptyOutcome: _render_query_empty,
    QueryResultOutcome: _render_query_result,
    QueryInvalidFiltersOutcome: _render_query_invalid_filters,
    CompletionNoticeOutcome: _render_completion_notice,
    SemanticUnavailableOutcome: _render_semantic_unavailable,
}


def render_kefu_outcome(outcome: KefuOutcome) -> str:
    """
    Exhaustive over the closed KefuOutcome union -- an outcome type with no
    registered renderer is a programming error (a new outcome class was
    added to core/kefu_outcomes.py without a matching renderer here), not a
    best-effort fallback. Raises rather than guessing at output.
    """
    renderer = _RENDERERS.get(type(outcome))
    if renderer is None:
        raise NotImplementedError(
            f"No renderer registered for outcome type {type(outcome).__name__} -- "
            "every KefuOutcome subtype in core/kefu_outcomes.py must have one here."
        )
    return renderer(outcome)
