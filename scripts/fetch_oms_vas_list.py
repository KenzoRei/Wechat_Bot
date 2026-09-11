"""
One-time (or occasional) lookup tool for OMS's VAS (增值服务) catalog --
NOT wired into the request-handling pipeline. clients/oms_client.py's
create_work_order() uses hardcoded VAS_LOGISTICS_FEE_BILL_ITEM_ID/
VAS_LOGISTICS_FEE_RULE_ID constants instead of a live lookup on every
work order (a value that essentially never changes doesn't need its own
API call, and its own failure modes, on every single label request).

Run this script once to find "物流费"'s real billItemId/ruleId for a given
OMS account, paste them into those constants, and only re-run if OMS's
VAS catalog ever changes for that account.

Endpoint: POST /v1/workOrder/vas/list
https://apidoc-oms.xlwms.com/reference/getworkordervaslistusingpost.md

Usage:
    python scripts/fetch_oms_vas_list.py --wh-code DE19713 --app-key ... --app-secret ...
    python scripts/fetch_oms_vas_list.py --wh-code DE19713 --app-key ... --app-secret ... --search 物流费
    python scripts/fetch_oms_vas_list.py --wh-code DE19713 --app-key ... --app-secret ... --all
"""
import argparse
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.oms_client import get_vas_list


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wh-code", required=True, help="OMS warehouse code (e.g. DE19713)")
    parser.add_argument("--app-key", required=True, help="OMS App_Key for the customer whose VAS catalog you're checking")
    parser.add_argument("--app-secret", required=True, help="OMS App_Secret for the same customer")
    parser.add_argument("--search", default="物流费", help="billItemName substring to search for (default: 物流费)")
    parser.add_argument("--all", action="store_true", help="print every item in the catalog, not just matches")
    args = parser.parse_args()

    items = get_vas_list(args.wh_code, args.app_key, args.app_secret)
    if not items:
        print(f"No VAS items returned for whCode={args.wh_code!r} -- empty catalog or invalid credentials.", file=sys.stderr)
        return 1

    print(f"{len(items)} VAS item(s) in catalog for whCode={args.wh_code!r}:\n")

    if args.all:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    matches = [item for item in items if args.search in str(item.get("billItemName", ""))]
    if not matches:
        print(f"No item matched search {args.search!r}. Full catalog:\n")
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 1

    for item in matches:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        print(
            f"\n--> Paste into clients/oms_client.py:\n"
            f"    VAS_LOGISTICS_FEE_BILL_ITEM_ID = {item.get('billItemId')!r}\n"
            f"    VAS_LOGISTICS_FEE_RULE_ID = {item.get('ruleId')!r}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
