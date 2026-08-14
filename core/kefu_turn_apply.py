"""Atomic, Kefu-native case-state application.

Unlike ``core.workflow_engine`` (whose helpers intentionally commit for the
Smart Robot path), every function here only mutates the supplied SQLAlchemy
session.  ``core.kefu_case_adapter`` owns the single commit which also stores
the case turn, execution ledger state, staff binding, and durable deliveries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session as DBSession

import config
from core import pre_confirm_validators
from core.confirmation import build_confirmation_message, build_display_name, build_confirmation_sections
from core.uchoice_rates import CHARGE_TYPE_DESCRIPTIONS
from core.uchoice_field_sanitization import sanitize_extracted_fields_before_persistence


# Built from CHARGE_TYPE_DESCRIPTIONS (core/uchoice_rates.py) so the actual
# distance/price definitions shown when asking for charge_type can never
# drift out of sync with the single source of truth used everywhere else
# (explain_service, the AI's charge-type explanation block, the
# upsert_address confirmation summary).
_CHARGE_TYPE_QUESTION = "请选择计费类型：\n" + "\n".join(
    f"- {desc}" for desc in CHARGE_TYPE_DESCRIPTIONS.values()
)

_FIELD_PROMPTS = {
    "sku_lines": ("商品及数量", "请提供商品、托盘/箱数及每托箱数。"),
    "destination_address_id": ("送货地址", "请提供或确认送货目的地。"),
    "charge_type": ("计费类型", _CHARGE_TYPE_QUESTION),
    "warehouse_code": ("所属仓库", "请选择所属仓库：JFK 或 DE。"),
    "reference_serial": ("申请编号", "请提供要处理的申请编号。"),
    "start_month": ("开始月份", "请提供查询的开始月份。"),
    "end_month": ("结束月份", "请提供查询的结束月份。"),
    "fulfillment_lines": ("实际出库明细", "请提供更正后的实际出库数量。"),
    "destination_packing_lines": ("目的仓装托方式", "请说明货物到达目的仓后的每托箱数和托盘数。"),
    "adjustment_lines": ("调整明细", "请提供商品编码、每托箱数，以及调整的托数（增加为正数，减少为负数）。"),
    "inventory_lines": ("实盘明细", "请提供完整实盘库存快照：每个商品的每托箱数及实际托数（未列出的规格将视为归零）。"),
    "move_lines": ("调拨明细", "请提供商品编码、源托盘规格（每托箱数）、目标托盘规格（每托箱数），以及移动的箱数。"),
}


_SERVICE_LABELS = {
    "uchoice_outbound_request": "出库申请",
    "uchoice_inbound_request": "入库申请",
    "upsert_address": "地址维护",
    "confirm_outbound_completion": "出库完成确认",
    "confirm_inbound_completion": "入库完成确认",
}


def _service_label(service: dict | None) -> str:
    """Shared display label lookup so missing-fields and cancellation
    wording stay consistent -- a bare fallback ("该申请"/"该服务") when the
    service is unresolved or unrecognized, never a raw internal name."""
    if service is None:
        return "该申请"
    return _SERVICE_LABELS.get(service.get("name"), "该服务")


def _render_missing_fields(service: dict, fields: dict) -> str:
    from core.kefu_outcomes import FieldPrompt, MissingFieldsOutcome
    from core.kefu_response_renderer import render_kefu_outcome

    missing = [
        name for name in ((service.get("input_schema") or {}).get("required") or [])
        if fields.get(name) in (None, "", [])
    ]
    prompts = []
    for name in dict.fromkeys(missing):
        label, question = _FIELD_PROMPTS.get(name, (name, f"请提供{name}。"))
        prompts.append(FieldPrompt(field=name, label=label, question=question))
    if not prompts:
        # A semantic ambiguity can keep a case open even when schema fields
        # appear present. Never fall back to AI prose.
        prompts.append(FieldPrompt(field="request_details", label="申请信息", question="请补充或确认申请信息。"))
    return render_kefu_outcome(MissingFieldsOutcome(service_label=_service_label(service), fields=tuple(prompts)))


_PENDING_CANDIDATE_KEYS = {
    "confirm_inbound_completion": ("pending_inbound_requests", "入库申请"),
    "confirm_outbound_completion": ("pending_outbound_requests", "出库申请"),
}


def _candidate_label(candidate: dict) -> str:
    detail = []
    if candidate.get("warehouse_code"):
        detail.append(f'{candidate["warehouse_code"]}仓')
    if candidate.get("sku_summary"):
        detail.append(candidate["sku_summary"])
    if candidate.get("destination"):
        detail.append(f'发往{candidate["destination"]}')
    serial = candidate.get("serial_number", "?")
    return f'{serial}（{"，".join(detail)}）' if detail else serial


def _resolve_reference_serial(context: dict, session, service: dict) -> str | None:
    """
    reference_serial ambiguity/absence for a targets_existing_request
    service is resolved deterministically against the injected pending-
    request candidate list, never left as a bare "请提供申请编号" prompt
    with no information about what the options even are. The AI's own
    candidate-listing instructions (ai/prompt_builder.py's candidates_block)
    live entirely in its `reply` field, which Kefu never sends; this is the
    deterministic
    replacement, observed missing live when multiple pending inbound
    requests existed and confirm_inbound_completion just asked for a bare
    serial number.

    Exactly one candidate is auto-resolved here too, as a backend backstop
    independent of whether the AI actually followed its own single-
    candidate auto-fill instruction (candidates_block's "恰好 1 条" rule) --
    a purely mechanical decision that doesn't need AI judgment at all.

    Returns the deterministic reply to send if resolution isn't complete
    (ambiguous or none pending), or None if reference_serial is already
    resolved -- either it already was, or exactly one candidate existed
    and got filled in here -- and the caller should proceed normally.
    """
    fields = session.collected_fields or {}
    if fields.get("reference_serial"):
        return None

    key_info = _PENDING_CANDIDATE_KEYS.get(service.get("name"))
    if key_info is None:
        return None
    candidate_key, label = key_info
    candidates = (context.get("uchoice_candidates") or {}).get(candidate_key) or []

    from core.kefu_outcomes import CandidateAmbiguousOutcome, CandidateNoneEligibleOutcome, CandidateOption
    from core.kefu_response_renderer import render_kefu_outcome

    if not candidates:
        return render_kefu_outcome(CandidateNoneEligibleOutcome(
            explanation=f"当前没有待处理的{label}，无需操作。"
        ))
    if len(candidates) == 1:
        serial = candidates[0].get("serial_number")
        if serial:
            session.collected_fields = {**fields, "reference_serial": serial}
            context["collected_fields"] = session.collected_fields
        return None

    options = tuple(
        CandidateOption(candidate_key=c["serial_number"], label=_candidate_label(c))
        for c in candidates if c.get("serial_number")
    )
    if len(options) < 2:
        return None
    return render_kefu_outcome(CandidateAmbiguousOutcome(
        prompt=f"当前有多个待处理的{label}，请问是哪一条？",
        options=options,
    ))


def _address_decision(db: DBSession, context: dict, ai_response):
    from core.kefu_response_renderer import validate_address_match
    candidates = (context.get("uchoice_candidates") or {}).get("addresses") or []
    return validate_address_match(ai_response, candidates), candidates


def _render_address_ambiguity(decision, candidates: list[dict]) -> str:
    from core.kefu_outcomes import AddressAmbiguousOutcome, AddressOption
    from core.kefu_response_renderer import render_kefu_outcome
    by_id = {c["address_id"]: c for c in candidates}
    options = []
    for index, address_id in enumerate(decision.ambiguous_address_ids, start=1):
        candidate = by_id[address_id]
        company = candidate.get("company_name")
        addr = candidate.get("addr") or ""
        label = f"{company}（{addr}）" if company else addr
        options.append(AddressOption(candidate_key=str(index), display_label=label))
    return render_kefu_outcome(AddressAmbiguousOutcome(options=tuple(options)))


def _pivot_to_address(db: DBSession, context: dict, old_session, old_log, guess: dict, services: list[dict]):
    """Atomically replace an unmatched outbound draft with address upkeep."""
    from models.kefu import CaseExecution
    from models.request_log import RequestLog
    from models.session import ConversationSession
    from core.kefu_outcomes import AddressPivotStartedOutcome, FieldPrompt
    from core.kefu_response_renderer import render_kefu_outcome

    address_service = next((s for s in services if s["name"] == "upsert_address"), None)
    if address_service is None:
        return None

    old_log.status = "cancelled"
    old_session.status = "cancelled"
    old_session.updated_at = datetime.now(timezone.utc)

    seed = {key: value for key, value in (guess or {}).items() if key in {"company_name", "addr"} and value}
    new_session = ConversationSession(
        wechat_openid=None,
        group_id=old_session.group_id,
        service_type_id=UUID(address_service["service_type_id"]),
        status="active",
        conversation_history=[{"role": "user", "content": context["content"]}],
        collected_fields=seed,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES),
        source_channel="kefu",
        opened_by_staff_id=old_session.opened_by_staff_id,
    )
    db.add(new_session)
    db.flush()
    new_log = RequestLog(
        wechat_openid=None,
        group_id=old_session.group_id,
        service_type_id=UUID(address_service["service_type_id"]),
        status="pending",
        raw_message=context["content"],
        # The triggering msgid already belongs to the cancelled outbound log
        # (request_log has a unique msgid index). CaseExecution/CaseTurn link
        # the atomic pivot; the new address log must not duplicate that key.
        wechat_msg_id=None,
        source_channel="kefu",
        submitted_by_staff_id=old_session.opened_by_staff_id,
        origin_session_id=new_session.session_id,
    )
    db.add(new_log)
    db.flush()
    new_session.request_log_id = new_log.log_id

    key = context.get("_kefu_execution_key")
    if key:
        db.query(CaseExecution).filter_by(execution_key=key, status="claimed").update({"session_id": new_session.session_id})

    missing = []
    if not seed.get("charge_type"):
        missing.append(FieldPrompt(field="charge_type", label="计费类型", question=_FIELD_PROMPTS["charge_type"][1]))
    if not seed.get("warehouse_code"):
        missing.append(FieldPrompt(field="warehouse_code", label="所属仓库", question=_FIELD_PROMPTS["warehouse_code"][1]))
    reply = render_kefu_outcome(AddressPivotStartedOutcome(
        cancelled_serial_number=old_log.serial_number,
        still_missing_fields=tuple(missing),
    ))
    _append(new_session, "assistant", reply)
    context["session_id"] = str(new_session.session_id)
    context["service_type_id"] = str(new_session.service_type_id)
    context["serial_number"] = new_log.serial_number
    context["collected_fields"] = seed
    context["_effective_service_name"] = "upsert_address"
    context["_reply"] = reply
    return reply


def _all_required_fields_present(service: dict, collected_fields: dict) -> bool:
    required = (service.get("input_schema") or {}).get("required") or []
    return all(collected_fields.get(field) not in (None, "", []) for field in required)


def _append(session, role: str, content: str) -> None:
    session.conversation_history = (session.conversation_history or []) + [
        {"role": role, "content": content}
    ]
    session.updated_at = datetime.now(timezone.utc)
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)


def _load_log(db: DBSession, session):
    if not session or not session.request_log_id:
        return None
    from models.request_log import RequestLog
    return db.get(RequestLog, session.request_log_id)


def _resolve_existing_target(db: DBSession, session, owned_log):
    from models.request_log import RequestLog
    reference = (session.collected_fields or {}).get("reference_serial")
    if not reference:
        return None, "未能确定要处理的申请编号，请重新描述或提供申请编号。"
    target = db.query(RequestLog).filter_by(serial_number=reference).first()
    if target is None:
        return None, f"未找到申请编号 {reference}，请确认后重试。"
    if target.status != "processing":
        return None, f"申请 {reference} 当前状态为「{target.status}」，无法处理。"
    session.request_log_id = target.log_id
    if owned_log is not None and owned_log.log_id != target.log_id:
        db.delete(owned_log)
        db.flush()
    return target, None


def _set_context_for_session(context: dict, session, log) -> None:
    context["session_id"] = str(session.session_id)
    context["service_type_id"] = str(session.service_type_id)
    context["serial_number"] = log.serial_number if log is not None else None
    context["collected_fields"] = session.collected_fields or {}
    context["customer_id"] = str(session.customer_id) if session.customer_id else None


def _apply_warehouse_default(service_name: str, session, context: dict) -> None:
    if service_name not in {"uchoice_inbound_request", "uchoice_outbound_request"}:
        return
    fields = session.collected_fields or {}
    if not fields.get("warehouse_code"):
        session.collected_fields = {
            **fields,
            "warehouse_code": "JFK",
            "_warehouse_auto_default": True,
        }
        context["collected_fields"] = session.collected_fields


def _resolve_outbound_pallet_defaults(db: DBSession, session, context: dict) -> str | None:
    """Non-committing counterpart of workflow_engine's stock-bucket resolver."""
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    fields = session.collected_fields or {}
    lines = fields.get("sku_lines") or []
    warehouse = fields.get("warehouse_code")
    resolved, clarifications = [], []
    changed = False
    labels = None
    for line in lines:
        if not isinstance(line, dict) or "box_count" in line:
            resolved.append(line)
            continue
        buckets = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse, sku_code=line.get("sku_code"))
            .filter(UchoiceStorage.pallet_count > 0)
            .order_by(UchoiceStorage.boxes_per_pallet.asc())
            .all()
        )
        count, stated = line.get("pallet_count"), line.get("boxes_per_pallet")
        # A stated pallet size describes the requested final packing, not the
        # shape of the source inventory. Preserve it; total-box feasibility
        # below decides whether boxes can be repalletized to satisfy it.
        if isinstance(stated, int) and not isinstance(stated, bool) and stated > 0 and count is not None:
            resolved.append(line)
            continue
        matching = next((bucket for bucket in buckets if bucket.boxes_per_pallet == stated), None)
        if matching is not None and count is not None and matching.pallet_count >= count:
            resolved.append(line)
        elif len(buckets) == 1:
            resolved.append({**line, "boxes_per_pallet": buckets[0].boxes_per_pallet, "_bpp_auto_default": True})
            changed = True
        elif len(buckets) > 1:
            labels = labels or sku_label_map(db)
            label = labels.get(line.get("sku_code"), line.get("sku_code"))
            options = "、".join(f"{b.boxes_per_pallet}箱/托（现有{b.pallet_count}托）" for b in buckets)
            clarifications.append(f"{label}：{options}")
            resolved.append(line)
        else:
            resolved.append(line)
    if clarifications:
        return f"请确认托盘规格——{'；'.join(clarifications)}。请告知您要哪一种。"
    if changed:
        session.collected_fields = {**fields, "sku_lines": resolved}
        context["collected_fields"] = session.collected_fields
    return None


def _resolve_outbound_completion_loose_picks(db: DBSession, session, context: dict) -> None:
    """
    Non-committing Kefu-native counterpart of workflow_engine's
    _resolve_outbound_loose_pick_defaults -- was missing entirely from this
    module, so every Kefu confirm_outbound_completion for an originally
    loose (box_count) SKU line always fell through to pre_confirm_
    validators.py's _loose_outbound_pick_required with an empty `picks`,
    which renders as "当前库存不足以自动分配所需数量" regardless of actual
    stock (observed live on REQ-20260812-001651: 524 real boxes on hand,
    only 15 needed, still rejected as "insufficient" -- the validator
    itself never checks stock, it only checks whether this resolution step
    already ran and filled in picks).

    Same greedy smallest-bucket-first resolution as the Smart Robot path,
    via the shared core.uchoice_context.resolve_loose_pick_defaults query.
    Must run before pre_confirm_validators.run for confirm_outbound_
    completion, mirroring _resolve_outbound_pallet_defaults's persist-
    before-validate ordering.
    """
    from core.uchoice_context import resolve_completion_target, resolve_loose_pick_defaults

    fields = session.collected_fields or {}
    reference_serial = fields.get("reference_serial")
    if not reference_serial:
        return
    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        return

    loose_box_counts = {
        l["sku_code"]: l["box_count"]
        for l in (original_fields.get("sku_lines") or []) if "box_count" in l
    }
    if not loose_box_counts:
        return

    warehouse_code = original_fields.get("warehouse_code")
    fulfillment_lines = fields.get("fulfillment_lines") or []
    by_sku = {l.get("sku_code"): l for l in fulfillment_lines}
    resolved_lines = list(fulfillment_lines)
    changed = False

    for sku, box_count_needed in loose_box_counts.items():
        existing = by_sku.get(sku)
        if existing and existing.get("picks"):
            continue  # already explicitly specified this turn
        picks = resolve_loose_pick_defaults(db, warehouse_code, sku, box_count_needed)
        if picks is None:
            continue  # insufficient stock -- leave unresolved, pre_confirm_validators will block with a clear message
        new_line = {"sku_code": sku, "picks": [{**p, "_auto_default": True} for p in picks]}
        if existing:
            resolved_lines = [new_line if l.get("sku_code") == sku else l for l in resolved_lines]
        else:
            resolved_lines.append(new_line)
        changed = True

    if changed:
        session.collected_fields = {**fields, "fulfillment_lines": resolved_lines}
        context["collected_fields"] = session.collected_fields


def _outbound_stock_error(db: DBSession, fields: dict) -> str | None:
    from core.uchoice_context import sku_label_map
    from core.uchoice_storage import available_box_count

    warehouse = fields.get("warehouse_code")
    required_by_sku: dict[str, int] = {}
    for line in fields.get("sku_lines") or []:
        if not isinstance(line, dict) or not line.get("sku_code"):
            continue
        if line.get("box_count") is not None:
            boxes = line.get("box_count")
        else:
            bpp, pallets = line.get("boxes_per_pallet"), line.get("pallet_count")
            if bpp is None or pallets is None:
                continue
            boxes = bpp * pallets
        required_by_sku[line["sku_code"]] = required_by_sku.get(line["sku_code"], 0) + boxes

    problems = []
    for sku, requested_boxes in sorted(required_by_sku.items()):
        available_boxes = available_box_count(db, warehouse, sku)
        if available_boxes < requested_boxes:
            problems.append((sku, available_boxes, requested_boxes))
    if not problems:
        return None
    labels = sku_label_map(db)
    from core.kefu_outcomes import InsufficientStockOutcome, StockShortage
    from core.kefu_response_renderer import render_kefu_outcome
    return render_kefu_outcome(InsufficientStockOutcome(
        warehouse_label=warehouse,
        shortages=tuple(StockShortage(
            sku_label=labels.get(sku, sku),
            requested_boxes=requested,
            available_boxes=available,
        ) for sku, available, requested in problems),
    ))


def _render_confirmation(db: DBSession, service: dict, session, log) -> str:
    from models.service import ServiceType

    service_type = db.get(ServiceType, session.service_type_id)
    return build_confirmation_message(
        serial_number=log.serial_number if log else "",
        service_display_name=build_display_name(service["name"], session.collected_fields or {}),
        sections=build_confirmation_sections(service["name"], session.collected_fields or {}, db),
        note=service_type.confirmation_note if service_type is not None else None,
    )


def _workflow_steps(db: DBSession, context: dict, service: dict, session) -> None:
    """Run the approved Kefu workflow without allowing an inner commit."""
    from handlers.registry import HANDLER_REGISTRY
    from handlers.uchoice.pdf_stub import GeneratePdfStubHandler
    from models.workflow import WorkflowStep

    workflow_id = UUID(service["workflow_id"])
    steps = db.query(WorkflowStep).filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order).all()
    context["result"] = {}
    context["request_log_id"] = str(session.request_log_id) if session.request_log_id else None
    context["_reply"] = ""
    context["_kefu_artifacts"] = []
    group_config = service.get("group_config") or {}

    for step in steps:
        merged = {**(step.config or {}), **group_config}
        if step.step_type == "generate_pdf_stub":
            doc_type = merged.get("doc_type")
            if doc_type == "outbound_instruction":
                artifact = GeneratePdfStubHandler._build_outbound_instruction_artifact(context, db)
                if artifact is not None:
                    context["_kefu_artifacts"].append({"doc_type": doc_type, "artifact": artifact})
                    context["result"].update({"pdf_status": "ready", "pdf_artifact_key": artifact["artifact_key"]})
            continue
        if step.step_type == "compute_invoice_handler":
            # Kefu-native path: send the workbook itself as a chat file
            # (via _kefu_artifacts -> core/kefu_delivery.py's enqueue_file),
            # never handlers/uchoice/queries.py's ComputeInvoiceHandler.
            # That handler's download-link/group-webhook behavior is
            # Smart Robot-only machinery (response_url can't carry a file at
            # all) -- irrelevant here since Kefu sends the file natively.
            from core.uchoice_invoice import compute_invoice
            from core.uchoice_invoice_export import build_invoice_artifact

            fields = session.collected_fields or {}
            warehouse_code = fields.get("warehouse_code")
            start_month = fields.get("start_month")
            end_month = fields.get("end_month")
            invoice = compute_invoice(db, warehouse_code, start_month, end_month)
            context["result"].update({k: (str(v) if hasattr(v, "quantize") else v) for k, v in invoice.items()})
            artifact = build_invoice_artifact(db, warehouse_code, start_month, end_month, context.get("request_log_id"))
            context["_kefu_artifacts"].append({"doc_type": "invoice_workbook", "artifact": artifact})
            context["result"]["invoice_artifact_key"] = artifact["artifact_key"]
            continue
        handler_class = HANDLER_REGISTRY.get(step.step_type)
        if handler_class is None:
            raise RuntimeError(f"No handler registered for step_type: {step.step_type!r}")
        if step.step_type == "upsert_address":
            merged["_defer_commit"] = True
        result = handler_class().handle(context, merged, db)
        context["result"].update(result or {})
        if (result or {}).get("_kefu_stop_workflow"):
            break


def _finish_execution(db: DBSession, context: dict, service: dict, session, log) -> str:
    _workflow_steps(db, context, service, session)
    now = datetime.now(timezone.utc)
    if context.get("result", {}).get("_kefu_stop_workflow") == "stock_changed":
        # Expected, committed business outcome: inventory was rechecked under
        # lock and changed since the summary. Keep the original request open,
        # return this completion case to collection, and discard every stale
        # packing/pick assumption before a corrected revision is confirmed.
        fields = dict(session.collected_fields or {})
        for key in ("fulfillment_lines", "source_picks", "destination_packing_lines"):
            fields.pop(key, None)
        session.collected_fields = fields
        context["collected_fields"] = fields
        session.status = "active"
        session.updated_at = now
        shortages = context["result"].get("stock_shortages") or []
        from core.uchoice_context import sku_label_map
        from core.kefu_outcomes import StockChangedOutcome, StockShortage
        from core.kefu_response_renderer import render_kefu_outcome
        labels = sku_label_map(db)
        reply = render_kefu_outcome(StockChangedOutcome(
            serial_number=log.serial_number if log is not None else (context.get("serial_number") or "未知申请"),
            shortages=tuple(StockShortage(
                sku_label=labels.get(item["sku_code"], item["sku_code"]),
                requested_boxes=item["requested_boxes"],
                available_boxes=item["available_boxes"],
            ) for item in shortages),
        ))
        context["_reply"] = reply
        _append(session, "assistant", reply)
        return reply
    if log is not None:
        if not service.get("awaits_completion", False):
            log.status = "success"
            log.completed_at = now
        else:
            log.status = "processing"
        log.result = context.get("result", {})
    session.status = "completed"
    session.updated_at = now
    reply = context.get("_reply") or "申请已处理。"
    _append(session, "assistant", reply)
    return reply


def confirm_kefu_turn(db: DBSession, context: dict, service: dict, session) -> str:
    """Apply one confirmed mutation; caller owns the guarded execution claim."""
    log = _load_log(db, session)
    _set_context_for_session(context, session, log)
    _append(session, "user", context["content"])
    if session.status != "pending_confirmation":
        reply = "该申请已处理或已关闭，不能重复确认。"
        _append(session, "assistant", reply)
        return reply
    # RequestLog supports ``processing``; ConversationSession deliberately
    # does not.  The surrounding CaseExecution claim/advisory lock owns this
    # confirmation attempt until the transaction reaches its terminal state.
    session.status = "active"
    if log is not None:
        log.status = "processing"
    return _finish_execution(db, context, service, session, log)


def cancel_kefu_turn(db: DBSession, context: dict, service: dict | None, session) -> str:
    from core.kefu_outcomes import ConfirmationCancelledOutcome
    from core.kefu_response_renderer import render_kefu_outcome

    if session is None:
        reply = render_kefu_outcome(ConfirmationCancelledOutcome(service_label=_service_label(service)))
        return reply
    log = _load_log(db, session)
    _set_context_for_session(context, session, log)
    _append(session, "user", context["content"])
    owns_log = service is None or not service.get("targets_existing_request", False)
    if owns_log and log is not None and log.status in ("pending", "processing"):
        log.status = "cancelled"
    session.status = "cancelled"
    # serial_number is only shown when the session genuinely owns the log
    # being closed -- for a targets_existing_request service (e.g. cancelling
    # a confirm_inbound_completion attempt), `log` is the ORIGINAL request,
    # which is untouched by this cancellation; showing its serial number
    # here would falsely imply that original request itself was cancelled.
    serial_number = log.serial_number if (owns_log and log is not None) else ""
    reply = render_kefu_outcome(ConfirmationCancelledOutcome(
        service_label=_service_label(service), serial_number=serial_number,
    ))
    _append(session, "assistant", reply)
    return reply


def apply_kefu_turn(db: DBSession, context: dict, ai_response, service: dict, session) -> str:
    """Create/continue a Kefu case, collect fields, and confirm or execute it."""
    from models.kefu import CaseExecution
    from models.request_log import RequestLog
    from models.session import ConversationSession

    if session is None:
        session = ConversationSession(
            wechat_openid=None,
            group_id=UUID(context["group_id"]),
            service_type_id=UUID(service["service_type_id"]),
            status="active",
            conversation_history=[{"role": "user", "content": context["content"]}],
            collected_fields={},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES),
            source_channel=context.get("source_channel") or "kefu",
            opened_by_staff_id=UUID(context["submitted_by_staff_id"]) if context.get("submitted_by_staff_id") else None,
        )
        db.add(session)
        db.flush()
        log = RequestLog(
            wechat_openid=None,
            group_id=UUID(context["group_id"]),
            service_type_id=UUID(service["service_type_id"]),
            status="pending",
            raw_message=context["content"],
            wechat_msg_id=context.get("msg_id") or None,
            source_channel=context.get("source_channel") or "kefu",
            submitted_by_staff_id=UUID(context["submitted_by_staff_id"]) if context.get("submitted_by_staff_id") else None,
            origin_session_id=session.session_id,
        )
        db.add(log)
        db.flush()
        session.request_log_id = log.log_id
        key = context.get("_kefu_execution_key")
        if key:
            db.query(CaseExecution).filter_by(execution_key=key, status="claimed").update({"session_id": session.session_id})
    else:
        log = _load_log(db, session)
        _append(session, "user", context["content"])

        # A case number may be allocated before its first service is chosen
        # (for example another staff member explicitly opens an empty case).
        # Bind that existing shell to the selected service and create its log
        # in this same transaction instead of producing a second session.
        if session.service_type_id is None:
            session.service_type_id = UUID(service["service_type_id"])
        if log is None:
            log = RequestLog(
                wechat_openid=None,
                group_id=UUID(context["group_id"]),
                service_type_id=UUID(service["service_type_id"]),
                status="pending",
                raw_message=context["content"],
                wechat_msg_id=context.get("msg_id") or None,
                source_channel=context.get("source_channel") or "kefu",
                submitted_by_staff_id=UUID(context["submitted_by_staff_id"]) if context.get("submitted_by_staff_id") else None,
                origin_session_id=session.session_id,
            )
            db.add(log)
            db.flush()
            session.request_log_id = log.log_id
        key = context.get("_kefu_execution_key")
        if key:
            db.query(CaseExecution).filter_by(execution_key=key, status="claimed").update(
                {"session_id": session.session_id}
            )

    _set_context_for_session(context, session, log)
    if ai_response.extracted_fields:
        extracted = sanitize_extracted_fields_before_persistence(
            service["name"], ai_response.extracted_fields, db, context.get("group_id")
        )
        session.collected_fields = {**(session.collected_fields or {}), **extracted}
        context["collected_fields"] = session.collected_fields

    # Stock impossibility is independent of customer/address collection. Once
    # SKU quantities and the warehouse are resolvable, reject immediately so
    # staff are not asked to maintain an address for goods that cannot ship.
    _apply_warehouse_default(service["name"], session, context)
    if service["name"] == "uchoice_outbound_request":
        stock_error = _outbound_stock_error(db, session.collected_fields or {})
        if stock_error:
            if log is not None:
                log.status = "cancelled"
            session.status = "cancelled"
            context["_reply"] = stock_error
            _append(session, "assistant", stock_error)
            return stock_error

        if not (session.collected_fields or {}).get("destination_address_id"):
            decision, address_candidates = _address_decision(db, context, ai_response)
            if decision.status == "matched":
                session.collected_fields = {
                    **(session.collected_fields or {}),
                    "destination_address_id": decision.matched_address_id,
                }
                context["collected_fields"] = session.collected_fields
            elif decision.status == "ambiguous":
                reply = _render_address_ambiguity(decision, address_candidates)
                context["_reply"] = reply
                _append(session, "assistant", reply)
                return reply
            elif decision.status == "unmatched" and (decision.unmatched_new_address or {}).get("addr"):
                pivot_reply = _pivot_to_address(
                    db, context, session, log, decision.unmatched_new_address,
                    context.get("allowed_services") or [],
                )
                if pivot_reply is not None:
                    return pivot_reply
                from core.kefu_outcomes import AddressPivotUnavailableOutcome
                from core.kefu_response_renderer import render_kefu_outcome
                reply = render_kefu_outcome(AddressPivotUnavailableOutcome(
                    escalation_note="该地址尚未收录，您当前没有新增地址权限。原出库申请仍保留，请联系管理员维护地址。"
                ))
                context["_reply"] = reply
                _append(session, "assistant", reply)
                return reply

    # Every current U-Choice service is performed on behalf of U-Choice
    # itself (the sole platform tenant today) -- there is no second
    # "customer" to identify for uchoice_inbound_request/uchoice_
    # outbound_request. `customer_id` identifies a future second tenant
    # (the same role group_id plays for Smart Robot), not which company in
    # the address book a request is for; requiring it here was a category
    # error, corrected after the user's direct clarification (see
    # docs/ai-collaboration/decisions.md's "Superseded or challenged
    # assumptions"). core/uchoice_customer.py's resolve_and_lock_customer
    # and core/uchoice_context.customer_candidates() remain as dormant,
    # reusable infrastructure for whenever a real second tenant exists.
    if service.get("targets_existing_request", False):
        serial_reply = _resolve_reference_serial(context, session, service)
        if serial_reply is not None:
            context["_reply"] = serial_reply
            _append(session, "assistant", serial_reply)
            return serial_reply

    # Deliberately NOT `ai_response.all_fields_collected or ...` -- that flag
    # is the AI's OWN claim, computed before _sanitize_extracted_fields_
    # before_persistence ever ran. If the field the AI based that claim on
    # gets silently dropped by sanitization (e.g. a malformed non-list
    # sku_lines value), an `or` here would let a stale, now-false claim
    # bypass the missing-fields check entirely -- observed live: the AI
    # claimed all_fields_collected=true while stuffing an invalid value into
    # sku_lines, sanitization correctly dropped it, but readiness still
    # passed on the strength of the discarded claim, and the turn sailed
    # into pre_confirm_validators instead of just re-asking for the field.
    # _all_required_fields_present alone is authoritative here: it already
    # returns true for a service with an empty required list, so nothing is
    # lost by not OR-ing in the AI's claim.
    ready = _all_required_fields_present(service, session.collected_fields or {})
    if not ready:
        reply = _render_missing_fields(service, session.collected_fields or {})
        context["_reply"] = reply
        _append(session, "assistant", reply)
        return reply

    if service.get("targets_existing_request", False):
        target, target_error = _resolve_existing_target(db, session, log)
        if target_error:
            session.status = "cancelled"
            context["_reply"] = target_error
            _append(session, "assistant", target_error)
            return target_error
        log = target
        context["serial_number"] = target.serial_number
        context["request_log_id"] = str(target.log_id)

    if service["name"] == "uchoice_outbound_request":
        clarification = _resolve_outbound_pallet_defaults(db, session, context)
        if clarification:
            context["_reply"] = clarification
            _append(session, "assistant", clarification)
            return clarification
    if service["name"] == "confirm_outbound_completion":
        _resolve_outbound_completion_loose_picks(db, session, context)
    validation_error = pre_confirm_validators.run(service["name"], context, session.collected_fields or {}, db)
    if validation_error:
        context["_reply"] = validation_error
        _append(session, "assistant", validation_error)
        return validation_error

    if service.get("requires_confirmation", True):
        reply = _render_confirmation(db, service, session, log)
        session.status = "pending_confirmation"
        context["_reply"] = reply
        _append(session, "assistant", reply)
        return reply

    if log is not None:
        log.status = "processing"
    return _finish_execution(db, context, service, session, log)
