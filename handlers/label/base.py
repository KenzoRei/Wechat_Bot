import re
from datetime import datetime, timezone
from handlers.base import BaseHandler
from clients.yidida_client import create_label


def _generate_ke_hu_dan_hao(context: dict) -> str:
    """
    Generates YiDiDa customer order number (客户单号).
    Format: ChatBot_<group8>_<user8>_<YYYYMMDD>_<serial3>
    Example: ChatBot_TestGrou_Simon_20260429_005
    """
    group = re.sub(r'\s+', '', context.get("group_description") or "")[:8]
    user  = re.sub(r'\s+', '', context.get("display_name") or "")[:8]
    date  = datetime.now(timezone.utc).strftime("%Y%m%d")

    serial_number = context.get("serial_number", "")
    serial_3 = serial_number.split("-")[-1][-3:] if serial_number else "001"

    return f"ChatBot_{group}_{user}_{date}_{serial_3}"


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
            "ke_hu_dan_hao":    _generate_ke_hu_dan_hao(context),
        }
        api_key = config.get("ydd_api_key")
        if not api_key:
            raise RuntimeError("ydd_api_key missing from group_service.config")

        result = create_label(carrier=carrier, fields=api_fields, api_key=api_key)

        # result contains tracking_number and label_url
        # stored in context["result"] for downstream steps (oms_record, reply_wechat)
        return result
