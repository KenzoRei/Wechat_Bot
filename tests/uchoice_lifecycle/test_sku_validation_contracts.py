"""SKU validation contracts for U-Choice lifecycle services."""

from types import SimpleNamespace

import pytest

from core import pre_confirm_validators, workflow_engine
from handlers.uchoice.storage_txns import (
    ApplyInboundStorageHandler,
    ApplyOutboundStorageHandler,
)
from models.uchoice import UchoiceSku, UchoiceStorage


class _Query:
    def __init__(self, model, catalog):
        self.model = model
        self.catalog = catalog
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def filter(self, *args):
        # No real buckets to filter in this catalog-only mock -- used by
        # pre_confirm_validators.py's stock-breakdown checks (move_storage,
        # confirm_outbound_completion), which only need an empty result here.
        return self

    def order_by(self, *args):
        return self

    def first(self):
        if self.model is UchoiceSku:
            sku_code = self.filters.get("sku_code")
            if sku_code in self.catalog:
                return SimpleNamespace(
                    sku_code=sku_code,
                    description=self.catalog[sku_code],
                )
        return None

    def all(self):
        if self.model is UchoiceSku:
            return [
                SimpleNamespace(sku_code=sku_code, description=description)
                for sku_code, description in self.catalog.items()
            ]
        if self.model is UchoiceStorage:
            return []
        return []


class _CatalogDB:
    def __init__(self):
        self.catalog = {"s1": "Product One", "s2": "Product Two"}

    def query(self, model):
        return _Query(model, self.catalog)


@pytest.fixture
def db():
    return _CatalogDB()


def _completion_target(monkeypatch, original_lines):
    target = SimpleNamespace(serial_number="REQ-TEST")
    original_fields = {"warehouse_code": "JFK", "sku_lines": original_lines}
    monkeypatch.setattr(
        "core.uchoice_context.resolve_completion_target",
        lambda _db, _serial: (target, original_fields),
    )
    monkeypatch.setattr(
        "core.uchoice_context.sku_label_map",
        lambda _db: {"s1": "Product One", "s2": "Product Two"},
    )
    return original_fields


def _palletized(sku_code, pallet_count=1):
    line = {"boxes_per_pallet": 80, "pallet_count": pallet_count}
    if sku_code is not None:
        line["sku_code"] = sku_code
    return line


@pytest.mark.parametrize("sku_code", [None, "fabricated-sku"])
def test_inbound_request_rejects_missing_or_unknown_sku(db, sku_code):
    error = pre_confirm_validators.run(
        "uchoice_inbound_request",
        {},
        {"warehouse_code": "JFK", "sku_lines": [_palletized(sku_code)]},
        db,
    )
    assert error is not None


def test_inbound_request_accepts_catalog_sku(db):
    error = pre_confirm_validators.run(
        "uchoice_inbound_request",
        {},
        {"warehouse_code": "JFK", "sku_lines": [_palletized("s1")]},
        db,
    )
    assert error is None


@pytest.mark.parametrize(
    "service_name,original_key",
    [
        ("confirm_inbound_completion", "received_lines"),
        ("confirm_outbound_completion", "fulfillment_lines"),
    ],
)
def test_completion_validates_inherited_lines_without_override(
    monkeypatch, db, service_name, original_key
):
    del original_key  # documents that the optional override is intentionally absent
    _completion_target(monkeypatch, [_palletized("fabricated-sku")])
    error = pre_confirm_validators.run(
        service_name,
        {},
        {"reference_serial": "REQ-TEST"},
        db,
    )
    assert error is not None


@pytest.mark.parametrize(
    "service_name,override_key",
    [
        ("confirm_inbound_completion", "received_lines"),
        ("confirm_outbound_completion", "fulfillment_lines"),
    ],
)
def test_completion_rejects_sku_substitution(
    monkeypatch, db, service_name, override_key
):
    _completion_target(monkeypatch, [_palletized("s1")])
    error = pre_confirm_validators.run(
        service_name,
        {},
        {
            "reference_serial": "REQ-TEST",
            override_key: [_palletized("s2", pallet_count=2)],
        },
        db,
    )
    assert error is not None


@pytest.mark.parametrize(
    "service_name,override_key",
    [
        ("confirm_inbound_completion", "received_lines"),
        ("confirm_outbound_completion", "fulfillment_lines"),
    ],
)
def test_completion_allows_quantity_change_for_original_sku(
    monkeypatch, db, service_name, override_key
):
    _completion_target(monkeypatch, [_palletized("s1")])
    error = pre_confirm_validators.run(
        service_name,
        {},
        {
            "reference_serial": "REQ-TEST",
            override_key: [_palletized("s1", pallet_count=2)],
        },
        db,
    )
    assert error is None


@pytest.mark.parametrize(
    "service_name",
    ["confirm_inbound_completion", "confirm_outbound_completion"],
)
def test_existing_loose_line_rule_still_blocks_unresolved_completion(
    monkeypatch, db, service_name
):
    _completion_target(
        monkeypatch,
        [{"sku_code": "s1", "box_count": 10}],
    )
    error = pre_confirm_validators.run(
        service_name,
        {},
        {"reference_serial": "REQ-TEST"},
        db,
    )
    assert error is not None


def test_inbound_handler_has_controlled_missing_sku_backstop(db):
    context = {
        "_uchoice_target": {
            "warehouse_code": "JFK",
            "original_fields": {"sku_lines": []},
        },
        "collected_fields": {"received_lines": [_palletized(None)]},
        "request_log_id": "REQ-TEST",
        "wechat_openid": "offline-test",
    }
    with pytest.raises(RuntimeError, match="sku_code"):
        ApplyInboundStorageHandler().handle(context, {}, db)


def test_outbound_handler_has_controlled_missing_sku_backstop(db):
    context = {
        "_uchoice_target": {
            "warehouse_code": "JFK",
            "original_fields": {"sku_lines": []},
        },
        "collected_fields": {"fulfillment_lines": [_palletized(None)]},
        "request_log_id": "REQ-TEST",
        "wechat_openid": "offline-test",
    }
    with pytest.raises(RuntimeError, match="sku_code"):
        ApplyOutboundStorageHandler().handle(context, {}, db)


@pytest.mark.parametrize(
    "service_name,field_name",
    [
        ("uchoice_inbound_request", "sku_lines"),
        ("confirm_inbound_completion", "received_lines"),
        ("confirm_outbound_completion", "fulfillment_lines"),
    ],
)
def test_codex_services_drop_fabricated_sku_before_persistence(
    db, service_name, field_name
):
    extracted = {
        "reference_serial": "REQ-TEST",
        field_name: [_palletized("fabricated-sku")],
    }

    cleaned = workflow_engine._sanitize_extracted_fields_before_persistence(
        service_name, extracted, db
    )

    assert cleaned.get(field_name) == []
    assert cleaned.get("reference_serial") == "REQ-TEST"


@pytest.mark.parametrize(
    "service_name,field_name",
    [
        ("uchoice_inbound_request", "sku_lines"),
        ("confirm_inbound_completion", "received_lines"),
        ("confirm_outbound_completion", "fulfillment_lines"),
    ],
)
def test_codex_services_omit_malformed_line_collection_before_persistence(
    db, service_name, field_name
):
    extracted = {
        "reference_serial": "REQ-TEST",
        field_name: {"sku_code": "s1"},
    }

    cleaned = workflow_engine._sanitize_extracted_fields_before_persistence(
        service_name, extracted, db
    )

    assert field_name not in cleaned
    assert cleaned.get("reference_serial") == "REQ-TEST"
