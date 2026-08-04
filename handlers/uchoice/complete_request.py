from handlers.base import BaseHandler


class CompleteExistingRequestHandler(BaseHandler):
    """
    confirm_inbound_completion / confirm_outbound_completion, final step
    before reply_wechat. The target request_log's own status/result fields
    are already handled centrally by workflow_engine's mark_success (since
    session.request_log_id was reassigned to point at the target log) — this
    handler's only job is the cross-group push into the ORIGINAL customer's
    group, which the confirming warehouseman isn't necessarily a member of.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.group import GroupConfig
        from clients.wechat_client import send_group_webhook_message

        target = context.get("_uchoice_target", {})
        group_id = target.get("group_id")
        webhook_url = None
        if group_id:
            group = db.query(GroupConfig).filter_by(group_id=group_id).first()
            webhook_url = group.group_robot_webhook_url if group else None

        serial_number = target.get("serial_number", context.get("serial_number", ""))
        direction_label = "入库" if target.get("direction") == "inbound" else "出库"

        if webhook_url:
            content = (
                f"✅ 您的{direction_label}申请已完成\n"
                f"申请编号：{serial_number}\n"
                f"如有问题请联系管理员。"
            )
            try:
                send_group_webhook_message(webhook_url, content)
            except RuntimeError as e:
                # Don't fail the whole completion just because the customer
                # notification failed to send — the warehouse-side completion
                # (storage, request_log) already succeeded by this point.
                print(f"[uchoice] group webhook push failed: {e}", flush=True)

        return {}
