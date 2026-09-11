"""
Verifies clients/yidida_client.py's dimension and weight-unit mapping
against the REAL YiDiDa Swagger schemas (twc.itdida.com/itdida-api/
swagger-ui.html), fetched and read directly on 2026-09-11:

- /yundans (importYundanUsingPOST): NO top-level changDu/kuanDu/gaoGao
  exist on YunDanModel at all -- every dimension this codebase previously
  sent there was silently dropped by YiDiDa (unrecognized JSON keys).
  Real dimensions live in danJianList[] as chang(长)/kuan(宽)/gao(高)/
  shiZhong(实重), and the endpoint's own description states
  "长度单位均为cm, 重量单位均为kg" -- length in CM, weight in KG.

- /price (queryPricesUsingPOST): dimensions live in a COMPLETELY
  different nested shape, unitModelList[], with ENGLISH field names
  (length/width/height/weight) -- not chang/kuan/gao. Top-level weight
  was already confirmed in KG in an earlier session against a real test
  script's own printed summary.

No real HTTP -- these test the pure body-building functions directly.
"""
import pytest

import clients.yidida_client as ydd_client

_LBS_TO_KG = 0.45359237
_IN_TO_CM = 2.54


def _shipment_fields(**overrides):
    base = {
        "shipper_name": "Jeff", "shipper_phone": "1234567890", "shipper_street": "1 Main St",
        "shipper_city": "Jamaica", "shipper_state": "NY", "shipper_zip": "11434",
        "recipient_name": "Simon", "recipient_phone": "1234567890", "recipient_street": "2 Elm St",
        "recipient_city": "South Plainfield", "recipient_state": "NJ", "recipient_zip": "07080",
        "weight_lbs": 10,
        "ydd_cust_id": "F900123", "ydd_channel_id": "UPS Ground NJ",
        "ke_hu_dan_hao": "ChatBot_F900123_test",
    }
    base.update(overrides)
    return base


# ── /yundans: _build_shipment_body ──────────────────────────────────────────

def test_shipment_body_converts_weight_lbs_to_kg():
    body = ydd_client._build_shipment_body(_shipment_fields(weight_lbs=10), "UPS Ground NJ")
    assert body["shouHuoShiZhong"] == pytest.approx(10 * _LBS_TO_KG)


def test_shipment_body_has_no_top_level_dimension_fields():
    """changDu/kuanDu/gaoGao don't exist anywhere in the real YunDanModel
    schema -- must never be sent, regardless of whether dims are given."""
    body = ydd_client._build_shipment_body(
        _shipment_fields(length_in=10, width_in=8, height_in=6), "UPS Ground NJ",
    )
    assert "changDu" not in body
    assert "kuanDu" not in body
    assert "gaoGao" not in body


def test_shipment_body_omits_dan_jian_list_without_any_dimension():
    body = ydd_client._build_shipment_body(_shipment_fields(), "UPS Ground NJ")
    assert "danJianList" not in body


def test_shipment_body_dan_jian_list_converts_inches_to_cm():
    body = ydd_client._build_shipment_body(
        _shipment_fields(length_in=10, width_in=8, height_in=6, weight_lbs=10), "UPS Ground NJ",
    )
    assert "danJianList" in body
    piece = body["danJianList"][0]
    assert piece["chang"] == pytest.approx(10 * _IN_TO_CM)
    assert piece["kuan"] == pytest.approx(8 * _IN_TO_CM)
    assert piece["gao"] == pytest.approx(6 * _IN_TO_CM)
    assert piece["shiZhong"] == pytest.approx(10 * _LBS_TO_KG)


def test_shipment_body_dan_jian_list_included_with_partial_dimensions():
    """Even one dimension present is enough to attach danJianList -- the
    piece's shiZhong is always included alongside whichever dims exist."""
    body = ydd_client._build_shipment_body(_shipment_fields(length_in=12), "UPS Ground NJ")
    piece = body["danJianList"][0]
    assert piece["chang"] == pytest.approx(12 * _IN_TO_CM)
    assert "kuan" not in piece
    assert "gao" not in piece
    assert "shiZhong" in piece


# ── /price: _build_price_query_body ─────────────────────────────────────────

def test_price_body_omits_unit_model_list_without_any_dimension():
    body = ydd_client._build_price_query_body(_shipment_fields(), "UPS Ground NJ")
    assert "unitModelList" not in body


def test_price_body_unit_model_list_uses_english_field_names_in_cm():
    """/price's nested dimension shape is unitModelList[].length/width/
    height -- NOT chang/kuan/gao (that's /yundans's shape, a different
    endpoint entirely)."""
    body = ydd_client._build_price_query_body(
        _shipment_fields(length_in=10, width_in=8, height_in=6, weight_lbs=10), "UPS Ground NJ",
    )
    assert "unitModelList" in body
    unit = body["unitModelList"][0]
    assert unit["length"] == pytest.approx(10 * _IN_TO_CM)
    assert unit["width"] == pytest.approx(8 * _IN_TO_CM)
    assert unit["height"] == pytest.approx(6 * _IN_TO_CM)
    assert unit["weight"] == pytest.approx(10 * _LBS_TO_KG)
    assert "chang" not in unit
    assert "kuan" not in unit
    assert "gao" not in unit


def test_price_body_top_level_weight_unaffected_by_dimension_presence():
    body = ydd_client._build_price_query_body(
        _shipment_fields(length_in=10, width_in=8, height_in=6, weight_lbs=10), "UPS Ground NJ",
    )
    assert body["weight"] == pytest.approx(10 * _LBS_TO_KG)
