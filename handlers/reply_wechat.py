from handlers.base import BaseHandler
from clients.wechat_client import send_message
from core.result_message import build_result_message, build_result_title, build_sections


class ReplyWeChatHandler(BaseHandler):
    """
    Last step in every workflow. Renders the final outcome message, built
    via core/result_message.py's registry (mirrors core/confirmation.py's
    registry-with-default-fallback pattern) — dispatch is by the service's
    real name, resolved once from context["service_type_id"], not by
    sniffing which keys happen to be present in context["result"].

    kefu-migration-plan.md / Codex round-88 finding 2: this step is the one
    remaining exit path that wrote straight to the Smart Robot client
    instead of the channel-neutral context["_reply"] convention every other
    exit path (core/workflow_engine.py's own send_message()) already uses --
    a successful Kefu workflow would finish with an empty case result since
    it has no response_url. Fixed to follow the identical pattern: always
    populate context["_reply"], and additionally send via response_url only
    when one is present (Smart Robot's own delivery path; a no-op for Kefu,
    which has no response_url and delivers via its own transport instead).
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        serial_number = context.get("serial_number", "")
        service_type_name = self._resolve_service_type_name(context, db)

        sections = build_sections(service_type_name, context, db)
        title = build_result_title(service_type_name, context)
        content = build_result_message(title, serial_number, sections)

        context["_reply"] = content
        response_url = context.get("response_url", "")
        if response_url:
            try:
                send_message(
                    wechat_openid=context["wechat_openid"],
                    content=content,
                    response_url=response_url,
                )
            except Exception as e:
                print(f"[reply_wechat] response_url send failed: {e}", flush=True)

        return {}

    @staticmethod
    def _resolve_service_type_name(context: dict, db) -> str | None:
        service_type_id = context.get("service_type_id")
        if not service_type_id:
            return None
        from models.service import ServiceType
        st = db.query(ServiceType).filter_by(service_type_id=service_type_id).first()
        return st.name if st else None
