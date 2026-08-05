from handlers.base import BaseHandler
from clients.wechat_client import send_message
from core.result_message import build_result_message, build_result_title, build_sections


class ReplyWeChatHandler(BaseHandler):
    """
    Last step in every workflow. Sends the final outcome message, built via
    core/result_message.py's registry (mirrors core/confirmation.py's
    registry-with-default-fallback pattern) — dispatch is by the service's
    real name, resolved once from context["service_type_id"], not by
    sniffing which keys happen to be present in context["result"].
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        serial_number = context.get("serial_number", "")
        service_type_name = self._resolve_service_type_name(context, db)

        sections = build_sections(service_type_name, context, db)
        title = build_result_title(service_type_name, context)
        content = build_result_message(title, serial_number, sections)

        send_message(
            wechat_openid=context["wechat_openid"],
            content=content,
            response_url=context.get("response_url", "")
        )

        return {}

    @staticmethod
    def _resolve_service_type_name(context: dict, db) -> str | None:
        service_type_id = context.get("service_type_id")
        if not service_type_id:
            return None
        from models.service import ServiceType
        st = db.query(ServiceType).filter_by(service_type_id=service_type_id).first()
        return st.name if st else None
