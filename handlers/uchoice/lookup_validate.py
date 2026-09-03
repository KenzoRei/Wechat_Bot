from handlers.base import BaseHandler
from models.request_log import RequestLog
from models.service import ServiceType
from core.uchoice_context import get_original_fields

_DIRECTION_SERVICE_NAMES = {
    "inbound":  "uchoice_inbound_request",
    "outbound": "uchoice_outbound_request",
}
_DIRECTION_LABELS = {
    "uchoice_inbound_request":  "入库",
    "uchoice_outbound_request": "出库",
}


class LookupAndValidateCompletionHandler(BaseHandler):
    """
    confirm_inbound_completion / confirm_outbound_completion, step 1.
    Re-validates the target request right before mutating storage: exists,
    still 'processing' (workflow_engine already checked this once when the
    confirmation template was built, but re-check here — this is the last
    gate before storage actually changes), the target's actual service type
    matches the direction being run (nothing previously checked this — a
    warehouseman could reference an outbound request's serial while running
    the inbound completion, and since both share the same sku_lines shape it
    would silently apply storage math in the wrong direction with no error),
    and the confirming warehouseman's own warehouse_code matches the
    original request's warehouse.

    Stashes the original request's collected_fields onto
    context["_uchoice_target"] for the storage-mutation and
    complete_existing_request steps that follow.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core.workflow_errors import TargetAlreadyResolvedError, TargetValidationError

        request_log_id = context.get("request_log_id")
        if not request_log_id:
            raise TargetValidationError("No target request resolved for this completion.")

        # Locked, refreshed fetch: populate_existing() is required, not
        # optional -- this exact row was very likely already loaded earlier
        # in the same turn (e.g. the unlocked pre-check in
        # core/workflow_engine.py's _resolve_target_request), so without it
        # SQLAlchemy's identity map would return the already-cached Python
        # object without refreshing its attributes from this locked read --
        # the SQL-level lock would still correctly serialize the
        # transactions, but the losing transaction would see its own stale
        # in-memory status instead of the winner's committed one, defeating
        # the lock's entire purpose. Held only for the DB-phase transaction
        # (see core/workflow_engine.py's split), released on that
        # transaction's commit -- by which point the row's terminal status
        # is already durable.
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
            # A losing race against a concurrent completion/cancellation
            # attempt on the same row -- a typed business conflict, not an
            # operational failure. Must never mark the target itself
            # failed; the caller (core/workflow_engine.py /
            # core/kefu_turn_apply.py) catches this and leaves the target
            # exactly as the winner left it.
            raise TargetAlreadyResolvedError(target.status, target.serial_number)

        # Every rejection from here on uses TargetValidationError, not a
        # bare RuntimeError -- the shared exception handling treats any
        # other exception as an operational failure and calls
        # mark_failed() on session.request_log_id, which for this
        # targets_existing_request session is this TARGET, not something
        # this session owns. A warehouseman referencing the wrong serial
        # (or one belonging to a warehouse they're not assigned to) must
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
                    f"与当前操作（确认{expected_label}）方向不符，无法处理。"
                )

        original_fields = get_original_fields(db, target)
        warehouse_code = original_fields.get("warehouse_code")

        caller_warehouses = context.get("warehouse_codes")
        if caller_warehouses is not None and warehouse_code and warehouse_code not in caller_warehouses:
            raise TargetValidationError(
                f"该申请属于 {warehouse_code} 仓库，与您的仓库权限（{'、'.join(caller_warehouses)}）不符。"
            )

        context["_uchoice_target"] = {
            "serial_number":   target.serial_number,
            "wechat_openid":   target.wechat_openid,
            "group_id":        str(target.group_id) if target.group_id else None,
            "warehouse_code":  warehouse_code,
            "original_fields": original_fields,
            "direction":       direction,
        }
        return {"warehouse_code": warehouse_code}
