import config
from handlers.base import BaseHandler
from clients.wechat_client import send_message


# Base URL for label download links — uses the deployed server URL
# Falls back to Render URL if not set
_LABEL_BASE_URL = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")


class ReplyWeChatHandler(BaseHandler):
    """
    Last step in every workflow. Sends a success message to the user.
    Includes 标签追踪号 (tracking number) and a label download link.
    """

    def handle(self, context: dict, config: dict) -> dict:
        result          = context.get("result", {})
        display_name    = context.get("display_name", "")
        tracking_number = result.get("tracking_number", "")
        serial_number   = context.get("serial_number", "")
        has_label       = bool(result.get("label_base64", ""))

        lines = [
            f"✅ {display_name}，您的申请已成功处理！",
            f"申请编号：{serial_number}",
            f"标签追踪号：{tracking_number}",
        ]

        if has_label:
            label_url = f"{_LABEL_BASE_URL}/labels/{serial_number}"
            lines.append(f"[点击下载标签]({label_url})")

        lines.append("如有问题请联系管理员。")

        send_message(
            wechat_openid=context["wechat_openid"],
            content="\n".join(lines),
            response_url=context.get("response_url", "")
        )

        return {}
