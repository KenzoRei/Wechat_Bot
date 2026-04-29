"""
YiDiDa Logistics API client.

Auth flow:
  POST /login  {username, password}  →  token (valid 2 days)
  Token passed as Authorization header in subsequent requests.

Shipment flow:
  POST /yundans  [YunDanModel]  →  tracking number + label URL
"""
import requests
import config

BASE_URL = config.YIDIDA_BASE_URL.rstrip("/")


# ── Authentication ─────────────────────────────────────────────────────────────

def _get_token(username: str, password: str) -> str:
    """
    Logs in to YiDiDa and returns the auth token.
    IMPORTANT: Must use form-encoded data (not JSON) — JSON returns 508 auth error.
    Token is valid for 2 days; for v1 we authenticate per request (simple, no caching).
    """
    url  = f"{BASE_URL}/login"
    resp = requests.post(
        url,
        data={"username": username, "password": password},  # form-encoded, NOT json=
        timeout=15
    )
    # Do NOT call raise_for_status() — YiDiDa uses non-2xx codes for business errors.
    # Read the JSON and check the success field instead.
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"YiDiDa login returned non-JSON (status {resp.status_code}): {resp.text[:200]}")

    if not data.get("success"):
        raise RuntimeError(f"YiDiDa login failed (status {resp.status_code}): {data.get('data')}")

    token = data.get("data")
    if not token:
        raise RuntimeError(f"YiDiDa login returned no token: {data}")
    return token


# ── Shipment creation ──────────────────────────────────────────────────────────

def create_label(carrier: str, fields: dict, api_key: str) -> dict:
    """
    Creates a shipping label via YiDiDa API.

    Args:
        carrier:  "fedex" or "ups" (determines default shouHuoQuDao if not in config)
        fields:   merged dict of collected_fields + group_service.config
                  Expected keys from group_service.config:
                    ydd_cust_id    → username for login
                    ydd_api_key    → password for login
                    ydd_channel_id → shouHuoQuDao (shipping channel name)
                  Expected keys from collected_fields:
                    shipper_name, shipper_corp_name, shipper_phone,
                    shipper_street, shipper_city, shipper_state, shipper_zip,
                    shipper_country (optional, default US)
                    recipient_name, recipient_corp_name, recipient_phone,
                    recipient_street, recipient_city, recipient_state,
                    recipient_zip, recipient_country (optional, default US)
                    weight_lbs, service_level (optional), reference_number (optional)
        api_key:  password for YiDiDa login (same as fields["ydd_api_key"])

    Returns:
        {"tracking_number": "...", "label_url": "..."}
    """
    username     = fields.get("ydd_cust_id", "")
    password     = api_key  # ydd_api_key is the login password
    shou_huo_qu_dao = fields.get("ydd_channel_id", "")

    if not username or not password or not shou_huo_qu_dao:
        raise RuntimeError("Missing YiDiDa credentials: ydd_cust_id, ydd_api_key, ydd_channel_id required")

    token = _get_token(username, password)

    body = _build_shipment_body(fields, shou_huo_qu_dao)

    url  = f"{BASE_URL}/yundans"
    resp = requests.post(
        url,
        json=[body],   # API accepts array of up to 10
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    return _parse_response(data)


def _build_shipment_body(fields: dict, shou_huo_qu_dao: str) -> dict:
    """Maps our collected_fields to YiDiDa's YunDanModel field names."""
    body = {
        # ── Shipping channel (required, admin-configured) ───────────────────
        "shouHuoQuDao": shou_huo_qu_dao,

        # ── Shipper info ────────────────────────────────────────────────────
        "jiJianRenMingCheng":    fields.get("shipper_name", ""),
        "jiJianGongSiMingCheng": fields.get("shipper_corp_name", ""),
        "jiJianRenDianHua":      fields.get("shipper_phone", ""),
        "jiJianRenDiZhi1":       fields.get("shipper_street", ""),
        "jiJianRenChengShi":     fields.get("shipper_city", ""),
        "jiJianRenState":        fields.get("shipper_state", ""),
        "jiJianRenYouBian":      fields.get("shipper_zip", ""),
        "guoJia":                fields.get("shipper_country", "US"),

        # ── Recipient info ──────────────────────────────────────────────────
        "shouJianRenXingMing":   fields.get("recipient_name", ""),
        "shouJianRenDianHua":    fields.get("recipient_phone", ""),
        "shouJianRenDiZhi1":     fields.get("recipient_street", ""),
        "shouJianRenChengShi":   fields.get("recipient_city", ""),
        "zhouMing":              fields.get("recipient_state", ""),
        "shouJianRenYouBian":    fields.get("recipient_zip", ""),

        # ── Package info ────────────────────────────────────────────────────
        "shouHuoShiZhong":       float(fields.get("weight_lbs", 0)),
        "jianShu":               1,
        "keHuDanHao":            fields.get("ke_hu_dan_hao") or fields.get("reference_number", ""),

        # ── Flags (standard defaults) ───────────────────────────────────────
        "requiredTrackNo":       True,
        "needValidateAddress":   False,
        "needDispatch":          False,
        "group":                 False,
    }

    # Optional: recipient company name
    if fields.get("recipient_corp_name"):
        body["shouJianRenGongSiMingCheng"] = fields["recipient_corp_name"]

    # Optional: dimensions
    if fields.get("length_in"):
        body["changDu"] = float(fields["length_in"])
    if fields.get("width_in"):
        body["kuanDu"] = float(fields["width_in"])
    if fields.get("height_in"):
        body["gaoGao"] = float(fields["height_in"])

    return body


def _parse_response(data: dict) -> dict:
    """
    Extracts tracking number and label from YiDiDa response.

    Key findings from API testing:
    - Tracking number field: zhuanDanHao (转单号), e.g. "871236980390"
    - labelUrl is always empty string — label is returned as base64 PDF in "label" field
    - waybillId is YiDiDa's internal ID (not needed for v1)
    """
    if not data.get("success"):
        raise RuntimeError(f"YiDiDa API error: {data}")

    if isinstance(data.get("data"), list) and data["data"]:
        item = data["data"][0]
        if item.get("code") != 200:
            raise RuntimeError(f"YiDiDa shipment failed: {item.get('message', item)}")
        return {
            "tracking_number": item.get("zhuanDanHao", ""),
            "label_base64":    item.get("label", ""),    # base64-encoded PDF
            "waybill_id":      item.get("waybillId", ""),
        }
    raise RuntimeError(f"YiDiDa unexpected response: {data}")
