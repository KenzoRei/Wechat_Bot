import requests
import config


def create_record(data: dict, api_key: str) -> dict:
    """
    Records a completed shipment in the OMS system.

    Args:
        data:    dict containing shipment details — at minimum:
                 serial_number, tracking_number, carrier, recipient fields.
        api_key: per-group OMS API key from group_service.config

    Returns dict with at minimum:
        {
            "oms_record_id": "..."
        }

    Raises RuntimeError on HTTP error or API-level failure.

    TODO: adapt the request body shape to match the actual OMS API spec.
    """
    url     = f"{config.OMS_BASE_URL}/records"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json"
    }

    payload = {
        "reference":       data.get("serial_number"),
        "carrier":         data.get("carrier"),
        "tracking_number": data.get("tracking_number"),
        "recipient_name":  data.get("recipient_name"),
        "street":          data.get("street"),
        "city":            data.get("city"),
        "state":           data.get("state"),
        "zip":             data.get("zip"),
        "weight_lbs":      data.get("weight_lbs"),
        "label_url":       data.get("label_url"),
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"OMS API error (create_record): {e}")

    result = response.json()

    # TODO: map actual OMS response keys to these standard field names
    return {
        "oms_record_id": result.get("id") or result.get("record_id"),
    }
