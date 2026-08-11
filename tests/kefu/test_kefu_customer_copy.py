from types import SimpleNamespace

from core.kefu_customer_copy import compose_staff_reply, render_customer_copy


class _Query:
    def filter_by(self, **kwargs):
        return self

    def first(self):
        return SimpleNamespace(company_name="ACME", addr="1 Main St")


class _DB:
    def query(self, model):
        return _Query()


def _session(service_fields):
    return SimpleNamespace(
        status="completed",
        customer_id="customer-1",
        collected_fields=service_fields,
    )


def test_outbound_customer_copy_is_built_from_allowlisted_fields(monkeypatch):
    monkeypatch.setattr("core.uchoice_context.sku_label_map", lambda db: {"S1": "Product 1"})
    session = _session({
        "warehouse_code": "JFK",
        "sku_lines": [{"sku_code": "S1", "pallet_count": 2, "boxes_per_pallet": 48}],
        "destination_address_id": "address-uuid",
        "charge_type": "internal-rate",
        "note": "SECRET INTERNAL NOTE",
        "matched_address_id": "internal-uuid",
    })
    text = render_customer_copy(
        _DB(),
        service_name="uchoice_outbound_request",
        session=session,
        request_log=SimpleNamespace(serial_number="REQ-1"),
        context={"submitted_by_staff_id": "secret-staff"},
    )
    assert "REQ-1" in text
    assert "Product 1：2 托（48 箱/托）" in text
    assert "ACME，1 Main St" in text
    for forbidden in ("SECRET", "internal-rate", "internal-uuid", "secret-staff", "customer-1"):
        assert forbidden not in text


def test_copy_is_only_emitted_for_completed_customer_scoped_cases():
    session = _session({})
    session.status = "pending_confirmation"
    assert render_customer_copy(
        _DB(), service_name="uchoice_inbound_request", session=session,
        request_log=SimpleNamespace(serial_number="REQ-1"), context={},
    ) is None
    session.status = "completed"
    assert render_customer_copy(
        _DB(), service_name="view_storage", session=session,
        request_log=SimpleNamespace(serial_number="REQ-1"), context={},
    ) is None


def test_staff_reply_marks_customer_copy_boundary():
    combined = compose_staff_reply("internal", "safe")
    assert combined == "internal\n\n【可复制给客户】\nsafe"
