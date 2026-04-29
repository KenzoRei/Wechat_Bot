"""
Manual test script for YiDiDa API.
Run from project root: python tests/test_ydd_api.py

Tests auth + shipment creation using the test credentials and LAX→DE sample data.
Does NOT require WeChat — calls YDD API directly.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json

BASE_URL = "http://twc.itdida.com/itdida-api"

# ── Credentials (test group) ──────────────────────────────────────────────────
USERNAME     = "F000179"
PASSWORD     = "abc12345"
SHOU_HUO_QU_DAO = "Fedex home delivery 洛杉矶渠道"


def test_auth():
    print("=== Step 1: Auth (form-encoded) ===")
    resp = requests.post(
        f"{BASE_URL}/login",
        data={"username": USERNAME, "password": PASSWORD},
        timeout=15
    )
    data = resp.json()
    print(f"Status: {resp.status_code} | Success: {data.get('success')}")
    if not data.get("success"):
        print(f"❌ Auth failed: {data.get('data')}")
        return None
    token = data.get("data")
    print(f"✅ Token: {token[:30]}...")
    return token


def test_create_shipment(token: str):
    print("\n=== Step 2: Create Shipment (LAX → DE, 11.03 lbs) ===")

    body = [{
        "shouHuoQuDao":         SHOU_HUO_QU_DAO,

        # Shipper — LAX warehouse
        "jiJianRenMingCheng":    "Paul Yang",
        "jiJianGongSiMingCheng": "TRANS WORLD LAX",
        "jiJianRenDianHua":      "626-242-5505",
        "jiJianRenDiZhi1":       "293 E REDONDO BEACH BLVD",
        "jiJianRenChengShi":     "GARDENA",
        "jiJianRenState":        "CA",
        "jiJianRenYouBian":      "90248",
        "guoJia":                "US",

        # Recipient — DE warehouse
        "shouJianRenXingMing":   "Zorro Zhang",
        "shouJianRenGongSiMingCheng": "TRANS WORLD DE",
        "shouJianRenDianHua":    "347-204-0602",
        "shouJianRenDiZhi1":     "201 GABOR DR",
        "shouJianRenChengShi":   "NEWARK",
        "zhouMing":              "DE",
        "shouJianRenYouBian":    "19711",

        # Package
        "shouHuoShiZhong":       11.03,
        "jianShu":               1,
        "keHuDanHao":            "TEST-001",

        # Flags
        "requiredTrackNo":       True,
        "needValidateAddress":   False,
        "needDispatch":          False,
        "group":                 False,
    }]

    resp = requests.post(
        f"{BASE_URL}/yundans",
        json=body,
        headers={
            "Authorization": token,
            "Content-Type":  "application/json"
        },
        timeout=30
    )

    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Response: {json.dumps(data, ensure_ascii=False, indent=2)}")

    if isinstance(data.get("data"), list) and data["data"]:
        item = data["data"][0]
        label_b64 = item.get("label", "")
        print(f"\n✅ Tracking number (zhuanDanHao): {item.get('zhuanDanHao', 'not found')}")
        print(f"✅ Waybill ID: {item.get('waybillId', 'not found')}")
        print(f"✅ Label (base64 PDF): {len(label_b64)} chars {'✓' if label_b64 else '✗'}")
        print(f"✅ Message: {item.get('message', '')}")
    else:
        print(f"\n❌ No shipment data: {data}")


if __name__ == "__main__":
    token = test_auth()
    if token:
        test_create_shipment(token)
    else:
        print("\nSkipping shipment test — auth failed.")
