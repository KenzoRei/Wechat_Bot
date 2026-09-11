"""
Direct coverage for clients/oms_client.py's HMAC-SHA256 request signing --
specifically that key-sorting is RECURSIVE, not top-level-only.

Real production failure this guards against: every OMS request body was
flat until workVasitemList (a list of nested dicts, added for the 物流费
VAS line) was introduced, at which point OMS started rejecting the
signature with [11006] 验签不通过 -- a top-level-only sort no longer
matched what OMS recomputes server-side on the full, nested JSON body.
Fixed by _deep_sort_keys, matching the proven-working reference
implementation in D:\\Project\\TWNJ_Helper\\core\\oms_client.py.

Pure functions, no HTTP -- not subject to tests/conftest.py's
block_operational_clients fixture (that only blocks create_work_order/
query_outbound_order themselves, not this private signing helper).
"""
import hashlib
import hmac
import json

import clients.oms_client as oms_client


def test_deep_sort_keys_sorts_top_level():
    result = oms_client._deep_sort_keys({"zebra": 1, "apple": 2, "Mango": 3})
    assert list(result.keys()) == ["apple", "Mango", "zebra"]


def test_deep_sort_keys_sorts_nested_dict():
    """The exact bug scenario: a nested dict's own keys (e.g. one
    workVasitemList entry) must also end up sorted, not left in
    insertion order."""
    result = oms_client._deep_sort_keys({
        "thirdNo": "T1",
        "workVasitemList": [
            {"qty": 5, "billItemId": 123, "ruleId": 456, "billItemName": "物流费"},
        ],
    })
    assert list(result.keys()) == ["thirdNo", "workVasitemList"]
    entry = result["workVasitemList"][0]
    assert list(entry.keys()) == ["billItemId", "billItemName", "qty", "ruleId"]


def test_deep_sort_keys_sorts_list_of_dicts_each_independently():
    result = oms_client._deep_sort_keys([{"b": 1, "a": 2}, {"d": 3, "c": 4}])
    assert list(result[0].keys()) == ["a", "b"]
    assert list(result[1].keys()) == ["c", "d"]


def test_deep_sort_keys_leaves_scalars_untouched():
    assert oms_client._deep_sort_keys("plain string") == "plain string"
    assert oms_client._deep_sort_keys(42) == 42
    assert oms_client._deep_sort_keys(None) is None


def test_sign_produces_recursively_sorted_json_and_matching_authcode():
    data = {
        "whCode": "DE19713",
        "workVasitemList": [{"qty": 10, "billItemId": 2, "ruleId": 1}],
    }
    req_time, data_json, authcode = oms_client._sign("app_key", "app_secret", data)

    # data_json must be the fully (recursively) sorted, compact JSON --
    # the exact string OMS will recompute and compare the signature against.
    expected_json = json.dumps(
        {"whCode": "DE19713", "workVasitemList": [{"billItemId": 2, "qty": 10, "ruleId": 1}]},
        ensure_ascii=False, separators=(",", ":"),
    )
    assert data_json == expected_json

    expected_authcode = hmac.new(
        "app_secret".encode("utf-8"),
        f"app_key{expected_json}{req_time}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert authcode == expected_authcode


def test_sign_flat_dict_unaffected_by_recursive_sort():
    """Regression guard: every pre-VAS request body was flat -- recursive
    sorting must behave identically to the old top-level-only sort for
    those, or every existing OMS call (query_outbound_order, plain
    create_work_order with no VAS line) would start failing too."""
    data = {"zebra": 1, "apple": 2, "thirdNo": "T1"}
    _, data_json, _ = oms_client._sign("k", "s", data)
    assert data_json == json.dumps(
        {"apple": 2, "thirdNo": "T1", "zebra": 1}, ensure_ascii=False, separators=(",", ":"),
    )
