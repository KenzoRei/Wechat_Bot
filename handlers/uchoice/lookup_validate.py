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
        request_log_id = context.get("request_log_id")
        if not request_log_id:
            raise RuntimeError("No target request resolved for this completion.")

        target = db.query(RequestLog).filter_by(log_id=request_log_id).first()
        if target is None:
            raise RuntimeError("目标申请不存在。")
        if target.status != "processing":
            raise RuntimeError(f"目标申请当前状态为「{target.status}」，无法处理。")

        direction = config.get("direction")
        expected_name = _DIRECTION_SERVICE_NAMES.get(direction)
        if expected_name:
            target_service = db.query(ServiceType).filter_by(service_type_id=target.service_type_id).first()
            if target_service and target_service.name != expected_name:
                actual_label = _DIRECTION_LABELS.get(target_service.name, target_service.name)
                expected_label = _DIRECTION_LABELS.get(expected_name, expected_name)
                raise RuntimeError(
                    f"申请 {target.serial_number} 是{actual_label}申请，"
                    f"与当前操作（确认{expected_label}）方向不符，无法处理。"
                )

        original_fields = get_original_fields(db, target)
        warehouse_code = original_fields.get("warehouse_code")

        caller_warehouse = context.get("warehouse_code")
        if caller_warehouse and warehouse_code and caller_warehouse != warehouse_code:
            raise RuntimeError(
                f"该申请属于 {warehouse_code} 仓库，与您的仓库权限（{caller_warehouse}）不符。"
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
