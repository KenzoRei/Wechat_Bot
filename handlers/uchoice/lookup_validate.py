from handlers.base import BaseHandler
from models.request_log import RequestLog
from models.session import ConversationSession


class LookupAndValidateCompletionHandler(BaseHandler):
    """
    confirm_inbound_completion / confirm_outbound_completion, step 1.
    Re-validates the target request right before mutating storage: exists,
    still 'processing' (workflow_engine already checked this once when the
    confirmation template was built, but re-check here — this is the last
    gate before storage actually changes), and the confirming warehouseman's
    own warehouse_code matches the original request's warehouse.

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

        # Both the customer's original session and the confirming
        # warehouseman's own completion session end up pointing at this same
        # request_log_id (by design — that's how targets_existing_request
        # routes mark_success at the right row). Filter on wechat_openid too,
        # or the newest-first order picks the warehouseman's own session
        # (wrong fields) instead of the customer's original one (right fields).
        original_session = (
            db.query(ConversationSession)
            .filter_by(request_log_id=target.log_id, wechat_openid=target.wechat_openid)
            .order_by(ConversationSession.created_at.desc())
            .first()
        )
        original_fields = original_session.collected_fields if original_session else {}
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
            "direction":       config.get("direction"),
        }
        return {"warehouse_code": warehouse_code}
