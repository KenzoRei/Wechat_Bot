from handlers.base import BaseHandler
from clients.wechat_client import send_message


class ReplyWeChatHandler(BaseHandler):
    """
    Last step in every workflow. Sends a success message to the user.
    Reads tracking_number from context["result"] populated by the label step.
    """

    def handle(self, context: dict, config: dict) -> dict:
        result          = context.get("result", {})
        display_name    = context.get("display_name", "")
        tracking_number = result.get("tracking_number", "")
        serial_number   = context.get("serial_number", "")

        message = (
            f"{display_name}，您的申请已成功处理！\n"
            f"申请编号：{serial_number}\n"
            f"运单号：{tracking_number}\n"
            f"如有问题请联系管理员。"
        )

        send_message(
            wechat_openid=context["wechat_openid"],
            content=message,
            response_url=context.get("response_url", "")
        )

        # reply_wechat produces no result for downstream steps
        return {}
