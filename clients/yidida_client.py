import requests
import config


def create_label(carrier: str, fields: dict, api_key: str) -> dict:
    """
    Creates a shipping label via YiDiDa API.

    Args:
        carrier: "fedex" or "ups"
        fields:  collected shipping fields (recipient_name, street, city, etc.)
        api_key: per-group YiDiDa API key from group_service.config

    Returns dict with at minimum:
        {
            "tracking_number": "...",
            "label_url":       "https://..."
        }

    Raises RuntimeError on HTTP error or API-level failure.

    TODO: adapt the request body shape to match the actual YiDiDa API spec.
    """
    url     = f"{config.YIDIDA_BASE_URL}/labels"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json"
    }

    payload = {
        "carrier": carrier.upper(),
        "recipient": {
            "name":    fields.get("recipient_name"),
            "phone":   fields.get("recipient_phone"),
            "street":  fields.get("street"),
            "city":    fields.get("city"),
            "state":   fields.get("state"),
            "zip":     fields.get("zip"),
        },
        "package": {
            "weight_lbs":   fields.get("weight_lbs"),
            "length_in":    fields.get("length_in"),
            "width_in":     fields.get("width_in"),
            "height_in":    fields.get("height_in"),
            "package_type": fields.get("package_type"),
        },
        "service_level":        fields.get("service_level"),
        "reference":            fields.get("reference"),
        "special_instructions": fields.get("special_instructions"),
        "ydd_cust_id":          fields.get("ydd_cust_id"),
        "ydd_channel_id":       fields.get("ydd_channel_id"),
        "ydd_account_code":     fields.get("ydd_account_code"),
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"YiDiDa API error (create_label): {e}")

    data = response.json()

    # TODO: map actual YiDiDa response keys to these standard field names
    return {
        "tracking_number": data.get("tracking_number"),
        "label_url":       data.get("label_url"),
    }
