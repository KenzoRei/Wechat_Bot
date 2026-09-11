"""
OMS (xlwms) API client.

Auth: HMAC-SHA256 signature
  1. Sort data fields alphabetically (case-insensitive), RECURSIVELY --
     every nested dict's own keys must also be sorted, not just the
     top level (confirmed the hard way: every request body sent by this
     client was flat until workVasitemList's nested item dicts were
     added, at which point OMS started rejecting the signature with
     [11006] 验签不通过 -- a top-level-only sort no longer matched what
     OMS recomputes server-side. A working reference implementation,
     D:\\Project\\TWNJ_Helper\\core\\oms_client.py's _deep_sort_keys,
     confirms nested dicts/lists must be sorted too.)
  2. Concatenate: appKey + sorted_data_json + reqTime
  3. HMAC-SHA256(key=appSecret, msg=concat) → hex → authcode
  4. authcode passed as GET query param; body contains appKey + reqTime + data

Base URL: https://api.xlwms.com/openapi
"""
import hmac
import hashlib
import json
import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

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

def _deep_sort_keys(obj):
    """Recursively sorts every dict's keys alphabetically (case-insensitive)
    -- OMS's signature verification recomputes the canonical JSON the same
    way, so a shallow (top-level-only) sort silently mismatches as soon as
    any nested dict (e.g. a workVasitemList entry) is involved."""
    if isinstance(obj, dict):
        return {k: _deep_sort_keys(obj[k]) for k in sorted(obj.keys(), key=lambda k: k.lower())}
    if isinstance(obj, list):
        return [_deep_sort_keys(item) for item in obj]
    return obj


def _sign(app_key: str, app_secret: str, data: dict) -> tuple[str, str, str]:
    """
    Returns (req_time, data_json, authcode).
    data_json is the JSON-encoded, recursively key-sorted data (used in
    request body -- sent pre-sorted so the server's raw-string signature
    verification sees the exact same byte sequence that was signed).
    authcode is the HMAC-SHA256 hex digest (used as query param).
    """
    req_time = str(int(time.time()))

    sorted_data = _deep_sort_keys(data)
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


# ── Value-added services (VAS) ────────────────────────────────────────────────
# https://apidoc-oms.xlwms.com/reference/getworkordervaslistusingpost.md
#
# get_vas_list is a thin, reusable wrapper around the real endpoint --
# kept here (not duplicated elsewhere) since it needs this module's own
# _post()/HMAC-signing machinery. It is NOT called by create_work_order
# below or anywhere else in the request-handling pipeline: a work order is
# created on every single label request, and this app deliberately does
# not want a live VAS-catalog lookup (with its own latency/failure modes)
# on that hot path for a value that essentially never changes. Instead,
# scripts/fetch_oms_vas_list.py is a standalone, manually-run tool that
# calls this function once to find "物流费"'s real billItemId/ruleId for
# this OMS account -- paste its output into the VAS_LOGISTICS_FEE_*
# constants below, then re-run only if OMS's VAS catalog ever changes.

def get_vas_list(wh_code: str, app_key: str, app_secret: str) -> list[dict]:
    """
    Returns the raw list of available VAS items for this warehouse, each a
    dict with billItemId, billItemName, billCode, billCodeDesc,
    currencyCode, unitPrice, ruleId.

    Endpoint: POST /v1/workOrder/vas/list
    """
    result = _post("/v1/workOrder/vas/list", app_key, app_secret, {"whCode": wh_code})
    return result.get("data") or []


# Hardcoded from a one-time run of scripts/fetch_oms_vas_list.py against
# this OMS account's real VAS catalog -- see that script's docstring to
# refresh these if the catalog ever changes. None means "not configured
# yet"; create_work_order below skips the VAS line (logged, non-fatal)
# rather than sending a nonsense billItemId/ruleId until these are filled in.
VAS_LOGISTICS_FEE_NAME        = "物流费"
VAS_LOGISTICS_FEE_BILL_ITEM_ID: int | None = 2050303347482492928
VAS_LOGISTICS_FEE_RULE_ID:      int | None = 2062901791998910464


# ── Endpoint 2b: Create work order ─────────────────────────────────────────────

def create_work_order(
    third_no:                    str,
    wh_code:                     str,
    tracking_number:             str,
    collected_fields:            dict,
    app_key:                     str,
    app_secret:                  str,
    associated_tracking_no:      str = "",
    associated_tracking_no_type: int = 0,
    unmatched_oms_order_no:      str = "",
    logistics_fee_qty:           int | None = None,
) -> str:
    """
    Creates an OMS work order (workTypeCode = 通用).

    Endpoint: POST /v1/workOrder/create
    Returns the created work order number (e.g. "WO260430-001").

    associated_tracking_no / associated_tracking_no_type:
        Leave both at defaults (empty / 0) for no-OMS-order shipments.
        Set associated_tracking_no_type=2, associated_tracking_no=oms_outbound_order_no
        when linking to an existing OMS outbound order.

    unmatched_oms_order_no:
        Set when the customer supplied an oms_outbound_order_no but OMS's
        own lookup could not find it -- preserves the raw value in the
        remark for manual reconciliation, rather than either silently
        dropping it or (the previous, incorrect behavior) still claiming a
        verified link via associated_tracking_no to an order OMS just said
        doesn't exist. Never set together with associated_tracking_no.

    logistics_fee_qty:
        When set (and > 0), attaches a "物流费" (logistics fee) VAS line
        with this quantity -- the ESTIMATED shipping cost (int(quote
        price) from YiDiDa's /price, see handlers/label/base.py), a
        placeholder finance updates manually once the carrier's real
        invoice comes out. Uses the hardcoded VAS_LOGISTICS_FEE_* constants
        above (see workVasitemList's schema at
        https://apidoc-oms.xlwms.com/reference/createworkorderusingpost.md:
        billItemId/ruleId/qty required, billItemName/omsRemark optional) --
        deliberately NOT a live VAS-catalog lookup on this hot path (see
        the constants' own comment for why). If they're still unconfigured
        (None), the VAS line is skipped (logged) rather than sending a
        nonsense billItemId/ruleId -- non-fatal, same as every other OMS
        failure mode in this pipeline.
    """
    remark = _build_remark(tracking_number, collected_fields, unmatched_oms_order_no)

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

    if logistics_fee_qty and logistics_fee_qty > 0:
        if VAS_LOGISTICS_FEE_BILL_ITEM_ID is None or VAS_LOGISTICS_FEE_RULE_ID is None:
            logger.warning(
                "VAS_LOGISTICS_FEE_BILL_ITEM_ID/RULE_ID not configured -- "
                "run scripts/fetch_oms_vas_list.py and fill them in. "
                "Creating work order without the 物流费 line."
            )
        else:
            data["workVasitemList"] = [{
                "billItemId":   VAS_LOGISTICS_FEE_BILL_ITEM_ID,
                "ruleId":       VAS_LOGISTICS_FEE_RULE_ID,
                "qty":          logistics_fee_qty,
                "billItemName": VAS_LOGISTICS_FEE_NAME,
            }]

    result = _post("/v1/workOrder/create", app_key, app_secret, data)
    return result.get("data", "")


def _build_remark(tracking_number: str, fields: dict, unmatched_oms_order_no: str = "") -> str:
    """Builds the work order remark with all shipment details."""
    lines = [
        f"标签追踪号: {tracking_number}",
    ]
    if unmatched_oms_order_no:
        lines.append(f"客户提供的OMS出库单号（系统未查到匹配）: {unmatched_oms_order_no}")
    lines += [
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
