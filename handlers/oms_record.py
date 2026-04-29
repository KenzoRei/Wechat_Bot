from handlers.base import BaseHandler
from clients.oms_client import create_record


class OMSRecordHandler(BaseHandler):
    """
    Records the completed shipment in the OMS system.
    Runs after the label handler — reads tracking_number and label_url
    from context["result"] which the label step populated.
    """

    def handle(self, context: dict, config: dict) -> dict:
        fields  = context.get("collected_fields", {})
        result  = context.get("result", {})

        data = {
            "serial_number":   context.get("serial_number"),
            "carrier":         fields.get("carrier") or config.get("carrier"),
            "tracking_number": result.get("tracking_number"),
            "label_url":       result.get("label_url"),
            "recipient_name":  fields.get("recipient_name"),
            "street":          fields.get("street"),
            "city":            fields.get("city"),
            "state":           fields.get("state"),
            "zip":             fields.get("zip"),
            "weight_lbs":      fields.get("weight_lbs"),
        }

        api_key = config.get("oms_api_key")
        if not api_key:
            raise RuntimeError("oms_api_key missing from group_service.config")

        oms_result = create_record(data, api_key=api_key)

        # oms_result contains oms_record_id
        return oms_result
