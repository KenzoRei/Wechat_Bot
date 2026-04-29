from handlers.base import BaseHandler
from clients.yidida_client import create_label


class YDDLabelBaseHandler(BaseHandler):
    """
    Shared label creation logic for all carriers via YiDiDa.
    FedExLabelHandler and UPSLabelHandler inherit from this —
    they only need to declare their carrier name.

    config (merged step + group config) expected keys:
        carrier          — "fedex" or "ups" (from step.config)
        ydd_cust_id      — YiDiDa customer ID (from group_service.config)
        ydd_channel_id   — YiDiDa channel ID  (from group_service.config)
        ydd_account_code — optional           (from group_service.config)
    """

    def handle(self, context: dict, config: dict) -> dict:
        fields = context.get("collected_fields", {})

        carrier = config.get("carrier")

        # apply defaults for optional fields not provided by customer
        default_service_level = "FEDEX_GROUND" if carrier == "fedex" else "UPS_GROUND"

        # merge group credentials + defaults into fields
        api_fields = {
            "service_level": default_service_level,   # overridden if customer provided it
            **fields,
            "ydd_cust_id":      config.get("ydd_cust_id"),
            "ydd_channel_id":   config.get("ydd_channel_id"),
            "ydd_account_code": config.get("ydd_account_code"),
        }
        api_key = config.get("ydd_api_key")
        if not api_key:
            raise RuntimeError("ydd_api_key missing from group_service.config")

        result = create_label(carrier=carrier, fields=api_fields, api_key=api_key)

        # result contains tracking_number and label_url
        # stored in context["result"] for downstream steps (oms_record, reply_wechat)
        return result
