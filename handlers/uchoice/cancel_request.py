"""
cancel_inbound_request / cancel_outbound_request handlers. Mirrors
handlers/uchoice/lookup_validate.py + handlers/uchoice/complete_request.py's
shape (lookup-and-validate step, then a mutation step), but transitions the
target to status='cancelled' instead of running storage handlers, and keeps
the cross-channel notification in a separate step (notify_cancelled_request,
below) rather than folding it into the state-mutation step -- see
core/workflow_engine.py's _UCHOICE_SPLIT_ELIGIBLE_SERVICES/
_SIDE_EFFECT_STEP_TYPES for why: a notification failure must never roll back
an already-committed cancellation.
"""
from datetime import datetime, timezone

from handlers.base import BaseHandler
from models.request_log import RequestLog
from models.service import ServiceType
from core.uchoice_context import get_original_fields
from core.workflow_errors import TargetAlreadyResolvedError, TargetValidationError

_DIRECTION_SERVICE_NAMES = {
    "inbound":  "uchoice_inbound_request",
    "outbound": "uchoice_outbound_request",
}
_DIRECTION_LABELS = {
    "uchoice_inbound_request":  "入库",
    "uchoice_outbound_request": "出库",
}


class LookupAndValidateCancellationHandler(BaseHandler):
    """
    cancel_inbound_request / cancel_outbound_request, step 1. Locked,
    refreshed fetch (see handlers/uchoice/lookup_validate.py's identical
    pattern for why populate_existing()+with_for_update() are both
    required), then re-validates: still 'processing' (raises
    TargetAlreadyResolvedError otherwise -- a typed business conflict the
    caller must not treat as an operational failure), matches the expected
    direction, belongs to the caller's own group, and is cancellable by the
    caller (admin, or the request's original creator, channel-aware and
    fail-closed on inconsistent provenance). Stashes context["_uchoice_target"]
    for the following steps, same shape lookup_validate.py already produces.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        request_log_id = context.get("request_log_id")
        if not request_log_id:
            raise TargetValidationError("No target request resolved for this cancellation.")

        target = (
            db.query(RequestLog)
            .filter_by(log_id=request_log_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if target is None:
            raise TargetValidationError("目标申请不存在。")
        if target.status != "processing":
            raise TargetAlreadyResolvedError(target.status, target.serial_number)

        # Every rejection from here on uses TargetValidationError, not a
        # bare RuntimeError -- the shared exception handling in
        # core/workflow_engine.py and core/kefu_turn_apply.py treats any
        # other exception as an operational failure and calls
        # mark_failed() on session.request_log_id, which for a
        # targets_existing_request session is this TARGET, not something
        # this session owns. A caller referencing the wrong serial (e.g. an
        # outbound serial typed into a cancel_inbound_request turn) must
        # never mark that unrelated, perfectly valid target 'failed'.
        direction = config.get("direction")
        expected_name = _DIRECTION_SERVICE_NAMES.get(direction)
        if expected_name:
            target_service = db.query(ServiceType).filter_by(service_type_id=target.service_type_id).first()
            # Fail closed: a target whose service_type row doesn't resolve
            # at all must never be treated as direction-matching by
            # omission.
            if not target_service or target_service.name != expected_name:
                actual_label = _DIRECTION_LABELS.get(
                    target_service.name if target_service else None, "未知类型"
                )
                expected_label = _DIRECTION_LABELS.get(expected_name, expected_name)
                raise TargetValidationError(
                    f"申请 {target.serial_number} 是{actual_label}申请，"
                    f"与当前操作（取消{expected_label}）方向不符，无法处理。"
                )

        if str(target.group_id) != str(context.get("group_id")):
            raise TargetValidationError("该申请不属于本群组，无法取消。")

        is_admin = context.get("role") == "admin"
        if not is_admin:
            if context.get("source_channel") == "kefu":
                is_owner = (
                    target.source_channel == "kefu"
                    and target.submitted_by_staff_id is not None
                    and str(target.submitted_by_staff_id) == context.get("submitted_by_staff_id")
                )
            else:
                is_owner = (
                    target.source_channel == "smart_robot"
                    and target.wechat_openid == context.get("wechat_openid")
                )
            if not is_owner:
                raise TargetValidationError("您没有权限取消该申请，只有申请人本人或管理员可以取消。")

        original_fields = get_original_fields(db, target)
        context["_uchoice_target"] = {
            "serial_number":         target.serial_number,
            "wechat_openid":         target.wechat_openid,
            "submitted_by_staff_id": str(target.submitted_by_staff_id) if target.submitted_by_staff_id else None,
            "source_channel":        target.source_channel,
            "group_id":              str(target.group_id) if target.group_id else None,
            "warehouse_code":        original_fields.get("warehouse_code"),
            "original_fields":       original_fields,
            "direction":             direction,
        }
        return {}


class CancelExistingRequestHandler(BaseHandler):
    """
    cancel_inbound_request / cancel_outbound_request, step 2. Sets the
    target's status/completed_at only -- does not touch target.result
    (preserves whatever was there, which is nothing yet for a request that
    was never completed) and makes no external call (that's the separated
    notify_cancelled_request step, so a delivery failure can never roll
    back this already-valid state change).
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        request_log_id = context.get("request_log_id")
        target = db.query(RequestLog).filter_by(log_id=request_log_id).first()
        if target is None:
            raise RuntimeError("目标申请不存在。")
        target.status = "cancelled"
        target.completed_at = datetime.now(timezone.utc)
        return {}


class NotifyCancelledRequestHandler(BaseHandler):
    """
    cancel_inbound_request / cancel_outbound_request, step 3 (side-effect
    phase). Best-effort, matching the existing precedent for this class of
    notification (handlers/uchoice/complete_request.py's cross-group push):
    any failure here is caught and logged, never re-raised, so a
    notification failure can never affect the already-committed
    cancellation on either channel.

    Notifies the ORIGINAL requester only when the canceller is someone
    else (an admin cancelling on their behalf) -- self-cancellation already
    got its own direct reply, a second identical notice would be redundant.

    Channel-aware dispatch:
    - Smart-Bot-originated target: calls send_group_webhook_message
      directly. Safe inline because this step type is in
      core.workflow_engine._SIDE_EFFECT_STEP_TYPES, which only runs after
      the DB-phase transaction (the cancellation itself) has committed.
    - Kefu-originated target, notified staff member: core.kefu_delivery
      .enqueue_text is a durable row insert either way (no HTTP call),
      never deferred.
    - The one case that genuinely needs deferral: the CURRENT pipeline is
      Kefu (context["source_channel"] == "kefu"), but the notification
      target is a Smart-Bot group webhook (an admin cancelling a Smart-Bot
      customer's request through Kefu). core.workflow_engine's own
      _SIDE_EFFECT_STEP_TYPES split does not apply to Kefu's pipeline --
      Kefu commits once, later, in core/kefu_case_adapter.py. So this
      appends to context["_deferred_webhook_notifications"] instead of
      calling the webhook inline; core/kefu_case_adapter.py's turn
      orchestration flushes that list after its own outer commit succeeds.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        target = context.get("_uchoice_target", {})
        if not target:
            return {}

        is_self_cancel = (
            (target.get("source_channel") == "smart_robot"
             and target.get("wechat_openid")
             and target.get("wechat_openid") == context.get("wechat_openid"))
            or
            (target.get("source_channel") == "kefu"
             and target.get("submitted_by_staff_id")
             and target.get("submitted_by_staff_id") == context.get("submitted_by_staff_id"))
        )
        if is_self_cancel:
            return {}

        serial_number = target.get("serial_number", context.get("serial_number", ""))
        direction_label = "入库" if target.get("direction") == "inbound" else "出库"
        content = (
            f"❌ 您的{direction_label}申请已取消\n"
            f"申请编号：{serial_number}\n"
            f"如有问题请联系管理员。"
        )

        if target.get("source_channel") == "smart_robot":
            self._notify_smart_robot(context, db, target, content)
        elif target.get("source_channel") == "kefu":
            self._notify_kefu(context, db, target, content)

        return {}

    @staticmethod
    def _notify_smart_robot(context: dict, db, target: dict, content: str) -> None:
        from models.group import GroupConfig
        from clients.wechat_client import send_group_webhook_message

        group_id = target.get("group_id")
        if not group_id:
            return
        group = db.query(GroupConfig).filter_by(group_id=group_id).first()
        webhook_url = group.group_robot_webhook_url if group else None
        if not webhook_url:
            return

        if context.get("source_channel") == "kefu":
            # Deferred: Kefu's own commit hasn't happened yet at this point
            # in the pipeline -- see this class's docstring.
            context.setdefault("_deferred_webhook_notifications", []).append(
                {"webhook_url": webhook_url, "content": content}
            )
            return

        try:
            send_group_webhook_message(webhook_url, content)
        except Exception as e:
            print(f"[uchoice] cancellation webhook push failed (non-fatal): {e}", flush=True)

    @staticmethod
    def _notify_kefu(context: dict, db, target: dict, content: str) -> None:
        from models.kefu import KefuStaff
        from core.kefu_delivery import enqueue_text

        staff_id = target.get("submitted_by_staff_id")
        if not staff_id:
            return
        staff = db.query(KefuStaff).filter_by(staff_id=staff_id).first()
        if staff is None or not staff.is_active:
            return

        try:
            enqueue_text(
                db,
                recipient_staff_id=staff.staff_id,
                idempotency_key=f"request-cancelled:{target.get('serial_number')}:{staff.staff_id}",
                text_content=content,
                request_log_id=context.get("request_log_id"),
            )
        except Exception as e:
            print(f"[uchoice] cancellation Kefu delivery enqueue failed (non-fatal): {e}", flush=True)
