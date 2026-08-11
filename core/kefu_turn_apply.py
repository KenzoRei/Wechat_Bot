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
from core import pre_confirm_validators, uchoice_constants
from core.confirmation import build_confirmation_message, build_display_name, build_sections
from core.uchoice_customer import resolve_and_lock_customer
from core.workflow_engine import _sanitize_extracted_fields_before_persistence


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


def _outbound_stock_error(db: DBSession, fields: dict) -> str | None:
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    warehouse = fields.get("warehouse_code")
    problems = []
    for line in fields.get("sku_lines") or []:
        if not isinstance(line, dict) or "box_count" in line or line.get("pallet_count") is None:
            continue
        bpp = line.get("boxes_per_pallet")
        bucket = None
        if bpp is not None:
            bucket = db.query(UchoiceStorage).filter_by(
                warehouse_code=warehouse,
                sku_code=line.get("sku_code"),
                boxes_per_pallet=bpp,
            ).first()
        available = bucket.pallet_count if bucket else 0
        if bpp is None or available < line["pallet_count"]:
            problems.append((line.get("sku_code"), bpp or "任意规格", available, line["pallet_count"]))
    if not problems:
        return None
    labels = sku_label_map(db)
    detail = "；".join(
        f"{labels.get(sku, sku)}@{bpp}/托 现有 {available} 托，申请 {requested} 托"
        for sku, bpp, available, requested in problems
    )
    return f"申请已取消：{warehouse} 仓库没有足够库存可满足此次出库——{detail}。请核实商品规格或数量后重新提交。"


def _render_confirmation(db: DBSession, service: dict, session, log) -> str:
    from models.service import ServiceType

    service_type = db.get(ServiceType, session.service_type_id)
    return build_confirmation_message(
        serial_number=log.serial_number if log else "",
        service_display_name=build_display_name(service["name"], session.collected_fields or {}),
        sections=build_sections(service["name"], session.collected_fields or {}, db),
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
        handler_class = HANDLER_REGISTRY.get(step.step_type)
        if handler_class is None:
            raise RuntimeError(f"No handler registered for step_type: {step.step_type!r}")
        if step.step_type == "upsert_address":
            merged["_defer_commit"] = True
        result = handler_class().handle(context, merged, db)
        context["result"].update(result or {})


def _finish_execution(db: DBSession, context: dict, service: dict, session, log) -> str:
    _workflow_steps(db, context, service, session)
    now = datetime.now(timezone.utc)
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
    session.status = "processing"
    if log is not None:
        log.status = "processing"
    return _finish_execution(db, context, service, session, log)


def cancel_kefu_turn(db: DBSession, context: dict, service: dict | None, session) -> str:
    reply = "已取消，您可以随时发起新申请。"
    if session is None:
        return reply
    log = _load_log(db, session)
    _set_context_for_session(context, session, log)
    _append(session, "user", context["content"])
    owns_log = service is None or not service.get("targets_existing_request", False)
    if owns_log and log is not None and log.status in ("pending", "processing"):
        log.status = "cancelled"
    session.status = "cancelled"
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
        extracted = _sanitize_extracted_fields_before_persistence(
            service["name"], ai_response.extracted_fields, db, context.get("group_id")
        )
        session.collected_fields = {**(session.collected_fields or {}), **extracted}
        context["collected_fields"] = session.collected_fields

    customer_id = None
    if service["name"] in uchoice_constants.CUSTOMER_SCOPED_KEFU_SERVICES:
        customer_id = resolve_and_lock_customer(
            session,
            session.collected_fields or {},
            (context.get("uchoice_candidates") or {}).get("customers", []),
        )
        context["customer_id"] = customer_id
        if customer_id and log is not None:
            log.customer_id = UUID(customer_id)

    ready = ai_response.all_fields_collected or _all_required_fields_present(service, session.collected_fields or {})
    if service["name"] in uchoice_constants.CUSTOMER_SCOPED_KEFU_SERVICES and customer_id is None:
        ready = False
    if not ready:
        reply = ai_response.reply
        context["_reply"] = reply
        _append(session, "assistant", reply)
        return reply

    _apply_warehouse_default(service["name"], session, context)
    if service["name"] == "uchoice_outbound_request":
        clarification = _resolve_outbound_pallet_defaults(db, session, context)
        if clarification:
            context["_reply"] = clarification
            _append(session, "assistant", clarification)
            return clarification
        stock_error = _outbound_stock_error(db, session.collected_fields or {})
        if stock_error:
            if log is not None:
                log.status = "cancelled"
            session.status = "cancelled"
            context["_reply"] = stock_error
            _append(session, "assistant", stock_error)
            return stock_error

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
