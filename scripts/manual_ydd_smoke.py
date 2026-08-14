"""
Manual smoke script for the YiDiDa API. Not a pytest test — deliberately
outside tests/ and its functions are not named test_* so default pytest
discovery never collects this file. It makes live external HTTP calls,
including creating a real shipment, so it also requires an explicit opt-in
env var so it can never run by accident.

Run from project root:
    RUN_LIVE_YDD_TESTS=1 YDD_TEST_USERNAME=... YDD_TEST_PASSWORD=... python scripts/manual_ydd_smoke.py

Tests auth + shipment creation using the test-group credentials and LAX->DE
sample data. Does NOT require WeChat -- calls the YDD API directly.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json

BASE_URL = "http://twc.itdida.com/itdida-api"
SHOU_HUO_QU_DAO = "Fedex home delivery 洛杉矶渠道"


def _require_opt_in() -> tuple[str, str]:
    if os.getenv("RUN_LIVE_YDD_TESTS") != "1":
        print("Refusing to run: set RUN_LIVE_YDD_TESTS=1 to confirm you want to make live "
              "calls against the YiDiDa API and create a real shipment.")
        sys.exit(1)
    username = os.getenv("YDD_TEST_USERNAME")
    password = os.getenv("YDD_TEST_PASSWORD")
    if not username or not password:
        print("Missing YDD_TEST_USERNAME / YDD_TEST_PASSWORD environment variables.")
        sys.exit(1)
    return username, password


def auth(username: str, password: str) -> str | None:
    print("=== Step 1: Auth (form-encoded) ===")
    resp = requests.post(
        f"{BASE_URL}/login",
        data={"username": username, "password": password},
        timeout=15
    )
    data = resp.json()
    print(f"Status: {resp.status_code} | Success: {data.get('success')}")
    if not data.get("success"):
        print(f"Auth failed: {data.get('data')}")
        return None
    token = data.get("data")
    print(f"Token: {token[:30]}...")
    return token


def create_shipment(token: str):
    print("\n=== Step 2: Create Shipment (LAX -> DE, 11.03 lbs) ===")

    body = [{
        "shouHuoQuDao":         SHOU_HUO_QU_DAO,

        # Shipper -- LAX warehouse
        "jiJianRenMingCheng":    "Paul Yang",
        "jiJianGongSiMingCheng": "TRANS WORLD LAX",
        "jiJianRenDianHua":      "626-242-5505",
        "jiJianRenDiZhi1":       "293 E REDONDO BEACH BLVD",
        "jiJianRenChengShi":     "GARDENA",
        "jiJianRenState":        "CA",
        "jiJianRenYouBian":      "90248",
        "guoJia":                "US",

        # Recipient -- DE warehouse
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
        print(f"\nTracking number (zhuanDanHao): {item.get('zhuanDanHao', 'not found')}")
        print(f"Waybill ID: {item.get('waybillId', 'not found')}")
        print(f"Label (base64 PDF): {len(label_b64)} chars {'ok' if label_b64 else 'missing'}")
        print(f"Message: {item.get('message', '')}")
    else:
        print(f"\nNo shipment data: {data}")


if __name__ == "__main__":
    username, password = _require_opt_in()
    token = auth(username, password)
    if token:
        create_shipment(token)
    else:
        print("\nSkipping shipment test -- auth failed.")
