from core import kefu_turn_apply


def test_pallet_request_compares_total_boxes_across_source_buckets(monkeypatch):
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"s2": "S2 Stretch Wrap"})
    monkeypatch.setattr("core.uchoice_storage.available_box_count", lambda db, wh, sku: 144)

    error = kefu_turn_apply._outbound_stock_error(object(), {
        "warehouse_code": "JFK",
        "sku_lines": [{"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 2}],
    })

    assert error is None


def test_pallet_request_rejects_by_requested_and_available_box_counts(monkeypatch):
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"s2": "S2 Stretch Wrap"})
    monkeypatch.setattr("core.uchoice_storage.available_box_count", lambda db, wh, sku: 143)

    error = kefu_turn_apply._outbound_stock_error(object(), {
        "warehouse_code": "JFK",
        "sku_lines": [{"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 2}],
    })

    assert "现有 143 箱" in error
    assert "申请 144 箱" in error


def test_repeated_sku_lines_are_aggregated_before_feasibility(monkeypatch):
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"s2": "S2 Stretch Wrap"})
    monkeypatch.setattr("core.uchoice_storage.available_box_count", lambda db, wh, sku: 143)

    error = kefu_turn_apply._outbound_stock_error(object(), {
        "warehouse_code": "JFK",
        "sku_lines": [
            {"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 1},
            {"sku_code": "s2", "box_count": 72},
        ],
    })

    assert "申请 144 箱" in error


def test_stated_final_packing_is_not_reinterpreted_as_source_bucket(monkeypatch):
    class Query:
        def filter_by(self, **kwargs): return self
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def all(self):
            return [
                type("Bucket", (), {"boxes_per_pallet": 40, "pallet_count": 2})(),
                type("Bucket", (), {"boxes_per_pallet": 80, "pallet_count": 1})(),
            ]

    class DB:
        def query(self, model): return Query()

    session = type("Session", (), {"collected_fields": {
        "warehouse_code": "JFK",
        "sku_lines": [{"sku_code": "s2", "boxes_per_pallet": 72, "pallet_count": 2}],
    }})()
    context = {}

    clarification = kefu_turn_apply._resolve_outbound_pallet_defaults(DB(), session, context)

    assert clarification is None
    assert session.collected_fields["sku_lines"][0]["boxes_per_pallet"] == 72
