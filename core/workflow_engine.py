from uuid import UUID
from sqlalchemy.orm import Session as DBSession

from ai.base import AIResponse
from core import session_manager, request_logger
from core.confirmation import build_confirmation_message
from clients.wechat_client import send_message
from handlers.registry import HANDLER_REGISTRY
from models.workflow import WorkflowStep
from models.service import ServiceType


def run(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    Main orchestrator. Called after the AI Provider Chain processes each message.
    Dispatches to the correct handler based on ai_response.intent.
    """
    intent = ai_response.intent

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


# ── Intent handlers ───────────────────────────────────────────────────────────

def _handle_new_request(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is starting a new service request.
    Reject if a session is already in progress; otherwise create one.
    """
    if context.get("session_id"):
        # session already open — reject and notify
        send_message(
            context["wechat_openid"],
            "你有一个未完成的申请，请先完成或取消后再提交新请求。"
        )
        return

    # find the matching service in the group's allowed list
    service = _find_service(context, ai_response.service_type_name)
    if service is None:
        send_message(
            context["wechat_openid"],
            f"抱歉，您的群组暂不支持该服务。如有疑问请联系管理员。"
        )
        return

    # create session
    session = session_manager.create_session(
        db,
        wechat_openid=context["wechat_openid"],
        group_id=UUID(context["group_id"]),
        initial_message=context["content"],
        service_type_id=UUID(service["service_type_id"])
    )

    # add AI reply to history and send to user
    session_manager.add_message(db, session, "assistant", ai_response.reply)
    send_message(context["wechat_openid"], ai_response.reply)


def _handle_continuation(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is providing more information for an existing session.
    Updates collected fields. If all fields are now collected, sends
    the confirmation template instead of the AI reply.
    """
    session = _get_session(context, db)
    if session is None:
        send_message(context["wechat_openid"], "抱歉，未找到您的申请，请重新发起。")
        return

    # record this user turn
    session_manager.add_message(db, session, "user", context["content"])
    session_manager.update_collected_fields(db, session, ai_response.extracted_fields)

    if ai_response.all_fields_collected:
        # create request_log to get the serial number
        log = request_logger.create_log(
            db,
            wechat_openid=context["wechat_openid"],
            group_id=UUID(context["group_id"]),
            service_type_id=session.service_type_id,
            raw_message=context["content"],
            wechat_msg_id=context["msg_id"]
        )

        # fetch confirmation_note for this service
        service_type = db.query(ServiceType).filter_by(
            service_type_id=session.service_type_id
        ).first()
        note = service_type.confirmation_note if service_type else None

        # build and send confirmation template
        confirmation_text = build_confirmation_message(
            service_type_name=service_type.name if service_type else "",
            collected_fields=session.collected_fields,
            serial_number=log.serial_number,
            confirmation_note=note
        )

        # record the confirmation message in history
        session_manager.add_message(db, session, "assistant", confirmation_text)

        # make serial_number available to downstream handlers
        context["serial_number"] = log.serial_number

        # link session to its request_log and move to pending_confirmation
        session.request_log_id = log.log_id
        session.status = "pending_confirmation"
        db.commit()

        send_message(context["wechat_openid"], confirmation_text)

    else:
        # still collecting — send AI's field-prompting reply
        session_manager.add_message(db, session, "assistant", ai_response.reply)
        send_message(context["wechat_openid"], ai_response.reply)


def _handle_confirm(context: dict, db: DBSession) -> None:
    """
    User confirmed the summary. Run all workflow steps in order.
    On success: complete session and request_log.
    On failure: mark both failed and notify user.
    """
    session = _get_session(context, db)
    if session is None or session.status != "pending_confirmation":
        send_message(context["wechat_openid"], "抱歉，未找到待确认的申请，请重新发起。")
        return

    try:
        _run_workflow_steps(context, session, db)
        # success — workflow's reply_wechat step sends the success message
        request_logger.mark_success(db, session.request_log_id, context.get("result", {}))
        session_manager.close_session(db, session, status="completed")

    except Exception as e:
        request_logger.mark_failed(db, session.request_log_id, error_detail=str(e))
        session_manager.close_session(db, session, status="failed")
        send_message(
            context["wechat_openid"],
            "申请处理失败，请稍后重试或联系管理员。"
        )


def _handle_cancel(context: dict, db: DBSession) -> None:
    """User explicitly cancelled. Close the session and notify."""
    session = _get_session(context, db)
    if session:
        session_manager.close_session(db, session, status="cancelled")
    send_message(context["wechat_openid"], "已取消，您可以随时发起新申请。")


def _handle_check_services(context: dict, ai_response: AIResponse) -> None:
    """AI already listed available services in its reply. Just send it."""
    send_message(context["wechat_openid"], ai_response.reply)


def _handle_unrecognized(context: dict, ai_response: AIResponse) -> None:
    """
    Message couldn't be classified. Send the AI's reply.
    Existing session stays open — user can continue or cancel.
    """
    send_message(context["wechat_openid"], ai_response.reply)


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
        step_result = handler.handle(context, merged_config)
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
