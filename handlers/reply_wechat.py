import config
from handlers.base import BaseHandler
from clients.wechat_client import send_message


# Base URL for label download links — uses the deployed server URL
# Falls back to Render URL if not set
_LABEL_BASE_URL = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")


class ReplyWeChatHandler(BaseHandler):
    """
    Last step in every workflow. Sends a success message to the user, shaped
    by whichever result keys the preceding steps produced — FedEx/UPS label
    results get the original tracking-number message, U-Choice read-only
    queries get their own rendering, everything else falls back to a generic
    "completed" message with whatever the workflow produced.
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        result        = context.get("result", {})
        display_name  = context.get("display_name", "")
        serial_number = context.get("serial_number", "")

        if "tracking_number" in result:
            lines = self._label_lines(display_name, serial_number, result)
        elif "storage_lines" in result:
            lines = self._storage_lines(serial_number, result)
        elif "history_lines" in result:
            lines = self._history_lines(serial_number, result)
        elif "total" in result:
            lines = self._invoice_lines(serial_number, result)
        else:
            lines = self._generic_lines(serial_number, result)

        send_message(
            wechat_openid=context["wechat_openid"],
            content="\n".join(lines),
            response_url=context.get("response_url", "")
        )

        return {}

    @staticmethod
    def _label_lines(display_name: str, serial_number: str, result: dict) -> list[str]:
        tracking_number = result.get("tracking_number", "")
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
        return lines

    @staticmethod
    def _storage_lines(serial_number: str, result: dict) -> list[str]:
        lines = [f"📦 当前库存（申请编号：{serial_number}）"]
        storage = result.get("storage_lines") or []
        if not storage:
            lines.append("（无匹配记录）")
        else:
            lines.extend(storage)
        return lines

    @staticmethod
    def _history_lines(serial_number: str, result: dict) -> list[str]:
        lines = [f"📜 库存变动记录（申请编号：{serial_number}）"]
        history = result.get("history_lines") or []
        if not history:
            lines.append("（本月无记录）")
        else:
            lines.extend(history)
        return lines

    @staticmethod
    def _invoice_lines(serial_number: str, result: dict) -> list[str]:
        return [
            f"🧾 {result.get('warehouse_code', '')} {result.get('target_month', '')} 月度费用报告（申请编号：{serial_number}）",
            f"运输费：${result.get('transportation_fee', 0)}",
            f"打托费：${result.get('palletization_fee', 0)}",
            f"拆包费：${result.get('unpacking_fee', 0)}",
            f"仓储费：${result.get('storage_fee', 0)}",
            f"合计：${result.get('total', 0)}",
        ]

    @staticmethod
    def _generic_lines(serial_number: str, result: dict) -> list[str]:
        lines = ["✅ 操作已完成", f"申请编号：{serial_number}"]
        skip_keys = {"pdf_url", "pdf_status", "warehouse_code"}
        for key, val in result.items():
            if key in skip_keys or val in (None, [], {}):
                continue
            lines.append(f"- {key}：{val}")
        lines.append("如有问题请联系管理员。")
        return lines
