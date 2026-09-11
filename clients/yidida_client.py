"""
YiDiDa Logistics API client.

Auth flow:
  POST /login  {username, password}  →  token (valid 2 days)
  Token passed as Authorization header in subsequent requests.

Shipment flow:
  POST /yundans  [YunDanModel]  →  tracking number + label URL
  (verified: this response carries NO pricing/fee/amount field of any
  kind -- "sales_amount" cannot come from here.)

Pricing flow (separate endpoint, confirmed against a real quote sample):
  POST /price  {priceZoneType, searchType, channel, wayTypeList, weight (KG),
                packageType, pieceCount, toCustomer}  →  one quote per
  specific channel, with a "moneyTotal" (USD total) field.
"""
import requests
import config


class ShipmentRejected(RuntimeError):
    """
    YiDiDa/the carrier rejected this specific shipment for a business
    reason (invalid phone, bad address, etc.) -- distinct from every other
    RuntimeError this module raises (auth failure, malformed response,
    unexpected shape), which represent a genuine infra problem and must
    keep propagating as plain RuntimeError so callers don't mistake one
    for the other. Carries the carrier's own raw message, untranslated --
    handlers/label/base.py is responsible for turning this into a
    user-facing instruction (core.workflow_errors.LabelCreationRejected).
    """

    def __init__(self, carrier_message: str):
        self.carrier_message = carrier_message
        super().__init__(carrier_message)

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


# ── Price quote ──────────────────────────────────────────────────────────────
# Genuinely separate from /yundans (label creation) -- confirmed against a
# real /yundans response that it carries no pricing field at all. Given a
# specific channel (shouHuoQuDao), YiDiDa returns exactly one quote.

_LBS_TO_KG = 0.45359237


def get_price_quote(fields: dict, api_key: str) -> dict:
    """
    Queries a shipping rate quote via /price. Raises RuntimeError on any
    failure (network, auth, business failure, or a response carrying no
    usable quote) -- same convention as create_label/_get_token. Callers
    (handlers/label/base.py) are responsible for deciding this is non-
    fatal to label creation, same as this codebase already treats OMS
    failures (see docs/reviews/active/2026-09-customer-service-and-label-
    pipeline/plan.md) -- this client layer never silently swallows a
    failure itself.

    Returns {"sales_amount": <float, USD>}.
    """
    username     = fields.get("ydd_cust_id", "")
    password     = api_key
    shou_huo_qu_dao = fields.get("ydd_channel_id", "")

    if not username or not password or not shou_huo_qu_dao:
        raise RuntimeError("Missing YiDiDa credentials: ydd_cust_id, ydd_api_key, ydd_channel_id required")

    token = _get_token(username, password)
    body  = _build_price_query_body(fields, shou_huo_qu_dao)

    url  = f"{BASE_URL}/price"
    resp = requests.post(
        url,
        json=body,
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    return _parse_price_response(data)


def _build_price_query_body(fields: dict, shou_huo_qu_dao: str) -> dict:
    """
    Maps our collected_fields to YiDiDa's /price query params.

    weight is in KILOGRAMS here (confirmed against a real /price test
    script's own printed summary: "Weight: {weight} kg") -- unlike
    /yundans's shouHuoShiZhong, which this codebase passes weight_lbs into
    directly with no conversion (a separate, pre-existing convention on
    that endpoint, not touched here).
    """
    weight_lbs = float(fields.get("weight_lbs", 0))
    return {
        "priceZoneType": 1,   # 1 = postal code
        "searchType":    2,   # 2 = customer (negotiated) pricing, not public
        "channel":       shou_huo_qu_dao,
        "wayTypeList":   [0],  # 0 = express export, matches label creation's own shipment type
        "weight":        weight_lbs * _LBS_TO_KG,
        "packageType":   1,
        "pieceCount":    1,
        "toCustomer": {
            "countryCode": fields.get("recipient_country", "US"),
            "postcode":    fields.get("recipient_zip", ""),
            "city":        fields.get("recipient_city", ""),
            "stateCode":   fields.get("recipient_state", ""),
        },
    }


def _parse_price_response(data: dict) -> dict:
    if not data.get("success"):
        raise RuntimeError(f"YiDiDa price query failed: {data}")

    quotes = data.get("data")
    if not isinstance(quotes, list) or not quotes:
        raise RuntimeError(f"YiDiDa price query returned no quotes: {data}")

    quote = quotes[0]
    if not quote.get("success", True):
        raise RuntimeError(f"YiDiDa price quote failed: {quote.get('errorMsg') or quote}")

    money_total = quote.get("moneyTotal")
    if money_total is None:
        raise RuntimeError(f"YiDiDa price quote missing moneyTotal: {quote}")

    return {"sales_amount": float(money_total)}


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
            raise ShipmentRejected(str(item.get("message", item)))
        return {
            "tracking_number": item.get("zhuanDanHao", ""),
            "label_base64":    item.get("label", ""),    # base64-encoded PDF
            "waybill_id":      item.get("waybillId", ""),
        }
    raise RuntimeError(f"YiDiDa unexpected response: {data}")
