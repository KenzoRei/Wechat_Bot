from uuid import UUID
from sqlalchemy.orm import Session as DBSession

from ai.base import AIResponse
from core import session_manager, request_logger, pre_confirm_validators
from core.confirmation import build_confirmation_message, build_display_name, build_sections
from clients.wechat_client import send_message as _send_raw


def send_message(context: dict, content: str) -> None:
    """
    Stores reply in context["_reply"] for synchronous return,
    AND calls the external API if response_url is available (for async use).
    """
    context["_reply"] = content
    # also try response_url if available (for workflow steps that run after label creation)
    response_url = context.get("response_url", "")
    if response_url:
        try:
            _send_raw(
                wechat_openid=context["wechat_openid"],
                content=content,
                response_url=response_url
            )
        except Exception as e:
            print(f"[workflow] response_url send failed: {e}", flush=True)
from handlers.registry import HANDLER_REGISTRY
from models.workflow import WorkflowStep
from models.service import ServiceType


def run_and_get_reply(context: dict, ai_response: AIResponse, db: DBSession) -> str:
    """
    Main orchestrator — synchronous version.
    Processes the message and returns the reply string directly
    instead of calling send_message(). The caller sends the reply
    as the encrypted webhook response.
    """
    intent = ai_response.intent
    context["_reply"] = ""  # handlers write reply here

    if intent == "new_request":
        _handle_new_request(context, ai_response, db)
    elif intent == "continuation":
        _handle_continuation(context, ai_response, db)
    elif intent == "confirm":
        _handle_confirm(context, db)
    elif intent == "cancel":
        _handle_cancel(context, db)
    elif intent == "check_services":
        _handle_check_services(context, ai_response)
    else:
        _handle_unrecognized(context, ai_response)

    return context.get("_reply", "")


def run(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """Legacy sync wrapper — kept for compatibility."""
    run_and_get_reply(context, ai_response, db)


# ── Intent handlers ───────────────────────────────────────────────────────────

def _handle_new_request(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is starting a new service request.
    Reject if a session is already in progress; otherwise create one.
    """
    if context.get("session_id"):
        # session already open — reject and notify
        send_message(context, "你有一个未完成的申请，请先完成或取消后再提交新请求。")
        return

    # find the matching service in the group's allowed list
    service = _find_service(context, ai_response.service_type_name)
    if service is None:
        send_message(context, "抱歉，您的群组暂不支持该服务。如有疑问请联系管理员。")
        return

    # create session
    session = session_manager.create_session(
        db,
        wechat_openid=context["wechat_openid"],
        group_id=UUID(context["group_id"]),
        initial_message=context["content"],
        service_type_id=UUID(service["service_type_id"])
    )

    # update context with new session id so downstream can use it
    context["session_id"] = str(session.session_id)

    # Log every resolved request immediately, regardless of eventual outcome —
    # EXCEPT for targets_existing_request services, which never own a log of
    # their own; they update the log they end up referencing instead.
    if not service.get("targets_existing_request", False):
        log = request_logger.create_log(
            db,
            wechat_openid=context["wechat_openid"],
            group_id=UUID(context["group_id"]),
            service_type_id=UUID(service["service_type_id"]),
            raw_message=context["content"],
            wechat_msg_id=context["msg_id"]
        )
        session.request_log_id = log.log_id
        context["serial_number"] = log.serial_number
        db.commit()

    # save any extracted fields from the first message
    if ai_response.extracted_fields:
        session_manager.update_collected_fields(db, session, ai_response.extracted_fields)

    # Q3 fix: if AI already has all fields from the first message, go straight to confirmation
    if ai_response.all_fields_collected:
        _on_all_fields_collected(context, ai_response, service, session, db)
    else:
        session_manager.add_message(db, session, "assistant", ai_response.reply)
        send_message(context, ai_response.reply)


def _on_all_fields_collected(
    context: dict,
    ai_response: AIResponse,
    service: dict,
    session,
    db: DBSession
) -> None:
    """
    Shared branch point once all_fields_collected=True — used by both
    _handle_new_request and _handle_continuation. Resolves a target request
    for targets_existing_request services, runs pre-confirmation validators,
    then either shows a confirmation template or executes immediately
    depending on requires_confirmation.
    """
    if service.get("targets_existing_request", False):
        target, error = _resolve_target_request(session, db)
        if error:
            send_message(context, error)
            return
        session.request_log_id = target.log_id
        context["serial_number"] = target.serial_number
        db.commit()

    error = pre_confirm_validators.run(service["name"], context, session.collected_fields, db)
    if error:
        send_message(context, error)
        return

    if service.get("requires_confirmation", True):
        _trigger_confirmation(context, session, db)
    else:
        _execute_workflow_and_finish(context, session, db)
        # _execute_workflow_and_finish sends its own reply via reply_wechat / failure message


def _resolve_target_request(session, db: DBSession):
    """
    For targets_existing_request services — resolves session.collected_fields
    ["reference_serial"] (already disambiguated by the AI against the injected
    candidate list) to an existing RequestLog. Returns (log, None) on success,
    (None, error_message) otherwise. Deeper validation (warehouse match,
    direction) happens in the service's own lookup_and_validate handler step,
    right before the mutation it protects.
    """
    from models.request_log import RequestLog

    reference_serial = session.collected_fields.get("reference_serial")
    if not reference_serial:
        return None, "未能确定要处理的申请编号，请重新描述或提供申请编号。"

    target = db.query(RequestLog).filter_by(serial_number=reference_serial).first()
    if target is None:
        return None, f"未找到申请编号 {reference_serial}，请确认后重试。"
    if target.status != "processing":
        return None, f"申请 {reference_serial} 当前状态为「{target.status}」，无法处理。"

    return target, None


def _trigger_confirmation(context: dict, session, db: DBSession) -> None:
    """
    Builds the confirmation template and moves the session to
    pending_confirmation. request_log already exists at this point (created
    in _handle_new_request, or resolved to a target in _on_all_fields_collected)
    — this function only renders and sends the template.
    """
    # context["serial_number"] is only populated within the turn the log was
    # created/resolved — on a later continuation turn it's a fresh context,
    # so fall back to the DB via session.request_log_id (same pattern as the
    # Q1 fix in _handle_confirm).
    if not context.get("serial_number") and session.request_log_id:
        from models.request_log import RequestLog
        log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
        if log:
            context["serial_number"] = log.serial_number

    service_type = db.query(ServiceType).filter_by(
        service_type_id=session.service_type_id
    ).first()
    note = service_type.confirmation_note if service_type else None
    service_type_name = service_type.name if service_type else ""

    display_name = build_display_name(service_type_name, session.collected_fields)
    sections = build_sections(service_type_name, session.collected_fields, db)

    confirmation_text = build_confirmation_message(
        serial_number=context.get("serial_number", ""),
        service_display_name=display_name,
        sections=sections,
        note=note
    )

    session_manager.add_message(db, session, "assistant", confirmation_text)
    session.status = "pending_confirmation"
    db.commit()

    send_message(context, confirmation_text)


def _handle_continuation(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is providing more information for an existing session.
    Updates collected fields. Triggers confirmation when all required fields collected.
    """
    session = _get_session(context, db)
    if session is None:
        send_message(context, "抱歉，未找到您的申请，请重新发起。")
        return

    service = _find_service_by_type_id(context, session.service_type_id)

    session_manager.add_message(db, session, "user", context["content"])
    session_manager.update_collected_fields(db, session, ai_response.extracted_fields)

    if ai_response.all_fields_collected and service is not None:
        _on_all_fields_collected(context, ai_response, service, session, db)
    else:
        session_manager.add_message(db, session, "assistant", ai_response.reply)
        send_message(context, ai_response.reply)


def _handle_confirm(context: dict, db: DBSession) -> None:
    """
    User confirmed the summary. Run all workflow steps in order.
    On success: complete session and request_log.
    On failure: mark both failed and notify user.
    """
    session = _get_session(context, db)
    if session is None or session.status != "pending_confirmation":
        send_message(context, "抱歉，未找到待确认的申请，请重新发起。")
        return

    # Q1 fix: serial_number is None in context when confirm message arrives
    # because it was set in the previous request's context but not persisted.
    # Load it from the linked request_log.
    if not context.get("serial_number") and session.request_log_id:
        from models.request_log import RequestLog
        log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
        if log:
            context["serial_number"] = log.serial_number

    _execute_workflow_and_finish(context, session, db)


def _execute_workflow_and_finish(context: dict, session, db: DBSession) -> None:
    """
    Shared by _handle_confirm and the requires_confirmation=false immediate
    path. Transitions the log to 'processing', runs workflow steps, marks
    success/failure, closes the session.

    awaits_completion services (uchoice_inbound_request/uchoice_outbound_request)
    are two-step: confirming only starts the request — it isn't actually done
    until a warehouseman later runs the matching targets_existing_request
    completion service against it. For those, a successful run leaves the log
    at 'processing' (only the session closes as completed); mark_success is
    skipped here and happens later, on the target log, when that completion
    service's own _execute_workflow_and_finish runs.
    """
    if session.request_log_id:
        request_logger.mark_processing(db, session.request_log_id)

    try:
        _run_workflow_steps(context, session, db)
        service = _find_service_by_type_id(context, session.service_type_id)
        awaits_completion = bool(service.get("awaits_completion", False)) if service else False
        if not awaits_completion:
            # success — workflow's reply_wechat step sends the success message
            request_logger.mark_success(db, session.request_log_id, context.get("result", {}))
        session_manager.close_session(db, session, status="completed")

    except Exception as e:
        import traceback
        print(f"[workflow] STEP FAILED: {e}", flush=True)
        traceback.print_exc()
        request_logger.mark_failed(db, session.request_log_id, error_detail=str(e))
        session_manager.close_session(db, session, status="failed")
        send_message(context, "申请处理失败，请稍后重试或联系管理员。")


def _handle_cancel(context: dict, db: DBSession) -> None:
    """
    User explicitly cancelled. Close the session and notify.
    Only marks request_log as cancelled if this session actually owns the
    log (i.e. its service isn't targets_existing_request) — cancelling a
    completion-confirmation session must never touch the original request
    it was merely referencing.
    """
    session = _get_session(context, db)
    if session:
        service = _find_service_by_type_id(context, session.service_type_id)
        owns_log = service is None or not service.get("targets_existing_request", False)
        if session.request_log_id and owns_log:
            request_logger.mark_cancelled(db, session.request_log_id)
        session_manager.close_session(db, session, status="cancelled")
    send_message(context, "已取消，您可以随时发起新申请。")


def _handle_check_services(context: dict, ai_response: AIResponse) -> None:
    """AI already listed available services in its reply. Just send it."""
    send_message(context, ai_response.reply)


def _handle_unrecognized(context: dict, ai_response: AIResponse) -> None:
    """
    Message couldn't be classified. Send the AI's reply.
    Existing session stays open — user can continue or cancel.
    """
    send_message(context, ai_response.reply)


# ── Workflow step runner ──────────────────────────────────────────────────────

def _run_workflow_steps(context: dict, session, db: DBSession) -> None:
    """
    Loads and executes all steps for the session's workflow in order.
    Each step handler receives the full context dict and its step config.
    Results are accumulated in context["result"] for subsequent steps to read.
    """
    workflow_id = _get_workflow_id(context, session)
    if workflow_id is None:
        raise RuntimeError("No workflow found for this session's service type.")

    steps = (
        db.query(WorkflowStep)
        .filter_by(workflow_id=workflow_id)
        .order_by(WorkflowStep.step_order)
        .all()
    )

    context["result"] = {}
    context["request_log_id"] = str(session.request_log_id) if session.request_log_id else None

    # load group-level config for this service (ydd_cust_id, ydd_channel_id, etc.)
    group_config = _get_group_config(context, session)

    for step in steps:
        handler_class = HANDLER_REGISTRY.get(step.step_type)
        if handler_class is None:
            raise RuntimeError(f"No handler registered for step_type: '{step.step_type}'")

        # merge step-level config with group-level config.
        # group_config takes precedence — it carries credentials specific to this group.
        merged_config = {**step.config, **group_config}

        handler = handler_class()
        step_result = handler.handle(context, merged_config, db)
        context["result"].update(step_result)


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_session(context: dict, db: DBSession):
    """Reload the current session from DB using session_id in context."""
    from models.session import ConversationSession
    session_id = context.get("session_id")
    if not session_id:
        return None
    return db.query(ConversationSession).filter_by(session_id=session_id).first()


def _find_service(context: dict, service_type_name: str | None) -> dict | None:
    """
    Finds a service entry in the group's allowed_services list by name.
    Returns the dict (with service_type_id and workflow_id) or None.
    """
    if not service_type_name:
        return None
    for service in context.get("allowed_services", []):
        if service["name"] == service_type_name:
            return service
    return None


def _find_service_by_type_id(context: dict, service_type_id) -> dict | None:
    """Finds a service entry in allowed_services by service_type_id (UUID or str)."""
    if service_type_id is None:
        return None
    target = str(service_type_id)
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == target:
            return service
    return None


def _get_workflow_id(context: dict, session) -> UUID | None:
    """
    Finds the workflow_id for the session's service type
    from the context's allowed_services list.
    """
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == str(session.service_type_id):
            return UUID(service["workflow_id"])
    return None


def _get_group_config(context: dict, session) -> dict:
    """
    Returns the group-specific config for the session's service type.
    This contains credentials like ydd_cust_id, ydd_channel_id.
    Merged with step.config before passing to each handler.
    """
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == str(session.service_type_id):
            return service.get("group_config", {})
    return {}
