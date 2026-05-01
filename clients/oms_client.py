"""
OMS (xlwms) API client.

Auth: HMAC-SHA256 signature
  1. Sort data fields alphabetically (case-insensitive)
  2. Concatenate: appKey + sorted_data_json + reqTime
  3. HMAC-SHA256(key=appSecret, msg=concat) → hex → authcode
  4. authcode passed as GET query param; body contains appKey + reqTime + data

Base URL: https://api.xlwms.com/openapi
"""
import hmac
import hashlib
import json
import time
import requests
import config

BASE_URL = config.OMS_BASE_URL.rstrip("/")

# Valid workTypeCode values (int IDs from OMS):
#   1906097348226387968  盘库
#   1906097391889104896  换标
#   1906097515340062720  通用  ← used for all shipment work orders
#   1936156535100596224  拍照
#   1936156562816544768  销毁
#   1937121136541892608  产品调整单
WORK_TYPE_GENERAL = 1906097515340062720


# ── Auth ───────────────────────────────────────────────────────────────────────

def _sign(app_key: str, app_secret: str, data: dict) -> tuple[str, str, str]:
    """
    Returns (req_time, data_json, authcode).
    data_json is the JSON-encoded sorted data (used in request body).
    authcode is the HMAC-SHA256 hex digest (used as query param).
    """
    req_time = str(int(time.time()))

    sorted_data = dict(sorted(data.items(), key=lambda x: x[0].lower()))
    data_json   = json.dumps(sorted_data, ensure_ascii=False, separators=(',', ':'))
    concat      = app_key + data_json + req_time

    authcode = hmac.new(
        app_secret.encode('utf-8'),
        concat.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return req_time, data_json, authcode


def _post(endpoint: str, app_key: str, app_secret: str, data: dict) -> dict:
    """
    Makes a signed POST request to the OMS API.
    authcode is passed as a GET query parameter.
    Body: {appKey, reqTime, data}
    """
    req_time, data_json, authcode = _sign(app_key, app_secret, data)

    url  = f"{BASE_URL}{endpoint}"
    body = {
        "appKey":  app_key,
        "reqTime": req_time,
        "data":    json.loads(data_json),
    }

    resp = requests.post(
        url,
        json=body,
        params={"authcode": authcode},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()

    # OMS returns code=200 (int) on success; error codes are strings like "11008"
    if str(result.get("code")) != "200":
        msg = result.get("msg") or result.get("message") or str(result)
        raise RuntimeError(f"OMS API error [{result.get('code')}]: {msg}")

    return result


# ── Endpoint 1: Query outbound order ──────────────────────────────────────────

def query_outbound_order(outbound_order_no: str, app_key: str, app_secret: str) -> dict:
    """
    Queries an OMS outbound order by number.
    Returns the first matching record dict (contains whCode, status, etc.),
    or raises RuntimeError if not found.

    Endpoint: POST /v1/outboundOrder/pageList
    """
    data = {
        "outboundOrderNos": outbound_order_no,
        "page":             1,
        "pageSize":         20,
    }
    result  = _post("/v1/outboundOrder/pageList", app_key, app_secret, data)
    records = result.get("data", {}).get("records", [])

    if not records:
        raise RuntimeError(f"OMS outbound order not found: {outbound_order_no}")

    return records[0]


# ── Endpoint 2: Create work order ─────────────────────────────────────────────

def create_work_order(
    third_no:                    str,
    wh_code:                     str,
    tracking_number:             str,
    collected_fields:            dict,
    app_key:                     str,
    app_secret:                  str,
    associated_tracking_no:      str = "",
    associated_tracking_no_type: int = 0,
) -> str:
    """
    Creates an OMS work order (workTypeCode = 通用).

    Endpoint: POST /v1/workOrder/create
    Returns the created work order number (e.g. "WO260430-001").

    associated_tracking_no / associated_tracking_no_type:
        Leave both at defaults (empty / 0) for no-OMS-order shipments.
        Set associated_tracking_no_type=2, associated_tracking_no=oms_outbound_order_no
        when linking to an existing OMS outbound order.
    """
    remark = _build_remark(tracking_number, collected_fields)

    data: dict = {
        "thirdNo":      third_no,
        "whCode":       wh_code,
        "workTypeCode": WORK_TYPE_GENERAL,
        "urgency":      1,
        "title":        f"Shipment Label: {third_no}",
        "remark":       remark,
    }

    if associated_tracking_no_type and associated_tracking_no:
        data["associatedTrackingNoType"] = associated_tracking_no_type
        data["associatedTrackingNo"]     = associated_tracking_no

    result = _post("/v1/workOrder/create", app_key, app_secret, data)
    return result.get("data", "")


def _build_remark(tracking_number: str, fields: dict) -> str:
    """Builds the work order remark with all shipment details."""
    lines = [
        f"标签追踪号: {tracking_number}",
        "",
        "发件人:",
        f"  姓名: {fields.get('shipper_name', '')}",
        f"  公司: {fields.get('shipper_corp_name', '')}",
        f"  电话: {fields.get('shipper_phone', '')}",
        f"  地址: {fields.get('shipper_street', '')}, {fields.get('shipper_city', '')}, "
               f"{fields.get('shipper_state', '')} {fields.get('shipper_zip', '')}, "
               f"{fields.get('shipper_country', 'US')}",
        "",
        "收件人:",
        f"  姓名: {fields.get('recipient_name', '')}",
        f"  公司: {fields.get('recipient_corp_name', '')}",
        f"  电话: {fields.get('recipient_phone', '')}",
        f"  地址: {fields.get('recipient_street', '')}, {fields.get('recipient_city', '')}, "
               f"{fields.get('recipient_state', '')} {fields.get('recipient_zip', '')}, "
               f"{fields.get('recipient_country', 'US')}",
        "",
        f"重量: {fields.get('weight_lbs', '')} lbs",
    ]
    return "\n".join(lines)
