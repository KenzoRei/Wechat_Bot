"""
Direct coverage for clients/oms_client.py's get_vas_list -- a thin,
reusable wrapper around OMS's VAS catalog endpoint, verified against the
real API docs:
  https://apidoc-oms.xlwms.com/reference/getworkordervaslistusingpost.md

get_vas_list itself is NOT called by create_work_order or anywhere in the
request-handling pipeline (see clients/oms_client.py's own comment on
VAS_LOGISTICS_FEE_BILL_ITEM_ID/RULE_ID for why: those are hardcoded from
a one-time run of scripts/fetch_oms_vas_list.py, not looked up live on
every work order). This function exists so that script -- and this test
file -- can call it without duplicating the HMAC-signing machinery in
_post(). No real HTTP: requests.post is monkeypatched. Deliberately not
blocked by tests/conftest.py's block_operational_clients fixture at the
client-module layer, same treatment as get_price_quote, since it's a
read-only lookup with no side effect (unlike create_work_order, which
IS blocked there).
"""
import clients.oms_client as oms_client


def test_logistics_fee_constants_are_configured():
    """
    Regression guard: these were None (VAS line silently skipped, logged)
    until a real lookup via scripts/fetch_oms_vas_list.py against this
    OMS account's catalog. Fails loudly if anyone ever reverts them to
    None, rather than that silently degrading back to "no VAS line" in
    production with only a log line to notice it.
    """
    assert oms_client.VAS_LOGISTICS_FEE_NAME == "物流费"
    assert oms_client.VAS_LOGISTICS_FEE_BILL_ITEM_ID == 2050303347482492928
    assert oms_client.VAS_LOGISTICS_FEE_RULE_ID == 2062901791998910464


def _mock_response(monkeypatch, json_body):
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return json_body
    monkeypatch.setattr(oms_client.requests, "post", lambda *a, **kw: FakeResp())


_VAS_LIST_RESPONSE = {
    "code": 200,
    "data": [
        {
            "billItemId": 1001,
            "billItemName": "打托费",
            "billCode": "PALLET",
            "billCodeDesc": "打托费",
            "currencyCode": "USD",
            "unitPrice": 15.0,
            "ruleId": 2001,
        },
        {
            "billItemId": 1002,
            "billItemName": "物流费",
            "billCode": "FREIGHT",
            "billCodeDesc": "物流费",
            "currencyCode": "USD",
            "unitPrice": 0.0,
            "ruleId": 2002,
        },
    ],
}


def test_get_vas_list_returns_raw_items(monkeypatch):
    _mock_response(monkeypatch, _VAS_LIST_RESPONSE)
    items = oms_client.get_vas_list("DE19713", "key", "secret")
    assert len(items) == 2
    assert items[1]["billItemName"] == "物流费"
    assert items[1]["billItemId"] == 1002
    assert items[1]["ruleId"] == 2002


def test_get_vas_list_returns_empty_list_on_empty_catalog(monkeypatch):
    _mock_response(monkeypatch, {"code": 200, "data": []})
    assert oms_client.get_vas_list("DE19713", "key", "secret") == []


def test_get_vas_list_returns_empty_list_when_data_missing(monkeypatch):
    """data key entirely absent (vs. an explicit empty list) must not crash."""
    _mock_response(monkeypatch, {"code": 200})
    assert oms_client.get_vas_list("DE19713", "key", "secret") == []
