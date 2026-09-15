"""
Direct coverage for clients/yidida_client.py's get_price_quote/_parse_price_response
-- the genuinely separate /price endpoint (confirmed against a real
/yundans response that label creation itself carries no pricing field at
all). No real HTTP: _get_token and the requests.post call are monkeypatched.
"""
import pytest

import clients.yidida_client as ydd_client


def _fields(**overrides):
    base = {
        "ydd_cust_id": "F900123", "ydd_channel_id": "UPS Ground NJ",
        "weight_lbs": 5, "recipient_country": "US", "recipient_zip": "90001",
        "recipient_city": "Los Angeles", "recipient_state": "CA",
        "shipper_country": "US", "shipper_zip": "33126",
        "shipper_city": "Doral", "shipper_state": "FL",
    }
    base.update(overrides)
    return base


def _mock_response(monkeypatch, json_body, status_ok=True):
    class FakeResp:
        def raise_for_status(self):
            if not status_ok:
                raise ydd_client.requests.exceptions.HTTPError("500")
        def json(self):
            return json_body
    monkeypatch.setattr(ydd_client.requests, "post", lambda *a, **kw: FakeResp())


def test_get_price_quote_extracts_money_total(monkeypatch):
    monkeypatch.setattr(ydd_client, "_get_token", lambda u, p: "tok")
    _mock_response(monkeypatch, {
        "success": True, "statusCode": 200,
        "data": [{"success": True, "moneyTotal": 7.83, "carrier": "UPS"}],
    })
    result = ydd_client.get_price_quote(fields=_fields(), api_key="pw")
    assert result == {"sales_amount": 7.83}


def test_build_price_query_body_converts_lbs_to_kg_and_maps_fields():
    body = ydd_client._build_price_query_body(_fields(weight_lbs=10), "UPS Ground NJ")
    assert body["channel"] == "UPS Ground NJ"
    assert body["weight"] == pytest.approx(10 * 0.45359237)
    assert body["toCustomer"] == {"countryCode": "US", "postcode": "90001", "city": "Los Angeles", "stateCode": "CA"}
    assert body["priceZoneType"] == 1
    assert body["searchType"] == 2
    assert body["wayTypeList"] == [0]


def test_build_price_query_body_includes_real_shipper_origin():
    """Regression: fromCustomer was missing entirely -- YiDiDa silently
    defaulted to some account-level origin instead of erroring, returning
    a normal-looking quote priced for the wrong zone/distance (observed
    live: a real Doral, FL shipment quoted as if departing from New York).
    """
    body = ydd_client._build_price_query_body(_fields(), "UPS Ground NJ")
    assert body["fromCustomer"] == {"countryCode": "US", "postcode": "33126", "city": "Doral", "stateCode": "FL"}


def test_missing_credentials_raises():
    with pytest.raises(RuntimeError, match="Missing YiDiDa credentials"):
        ydd_client.get_price_quote(fields={"ydd_cust_id": "", "ydd_channel_id": "ch"}, api_key="pw")


def test_top_level_failure_raises(monkeypatch):
    monkeypatch.setattr(ydd_client, "_get_token", lambda u, p: "tok")
    _mock_response(monkeypatch, {"success": False, "statusCode": 500})
    with pytest.raises(RuntimeError, match="price query failed"):
        ydd_client.get_price_quote(fields=_fields(), api_key="pw")


def test_empty_quotes_list_raises(monkeypatch):
    monkeypatch.setattr(ydd_client, "_get_token", lambda u, p: "tok")
    _mock_response(monkeypatch, {"success": True, "data": []})
    with pytest.raises(RuntimeError, match="no quotes"):
        ydd_client.get_price_quote(fields=_fields(), api_key="pw")


def test_quote_level_failure_raises(monkeypatch):
    monkeypatch.setattr(ydd_client, "_get_token", lambda u, p: "tok")
    _mock_response(monkeypatch, {"success": True, "data": [{"success": False, "errorMsg": "no rate found"}]})
    with pytest.raises(RuntimeError, match="no rate found"):
        ydd_client.get_price_quote(fields=_fields(), api_key="pw")


def test_missing_money_total_raises(monkeypatch):
    monkeypatch.setattr(ydd_client, "_get_token", lambda u, p: "tok")
    _mock_response(monkeypatch, {"success": True, "data": [{"success": True}]})
    with pytest.raises(RuntimeError, match="missing moneyTotal"):
        ydd_client.get_price_quote(fields=_fields(), api_key="pw")
