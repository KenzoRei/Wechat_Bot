"""Delivery order "to" card: optional receiver contact/phone line (no DB)."""
import datetime

from core import uchoice_delivery_order as d


def _capture(monkeypatch):
    seen = []
    monkeypatch.setattr(d, "_draw_wrapped_top", lambda c, text, *a, **k: seen.append(text))
    return seen


def _build(**kw):
    return d.build_delivery_order_pdf(
        consignee_company="ACME", consignee_addr="1 Main St",
        delivery_date=datetime.date(2026, 11, 5), product_lines=[],
        outbound_order_no="OB-1", **kw,
    )


def test_contact_and_phone_printed(monkeypatch):
    seen = _capture(monkeypatch)
    assert _build(consignee_contact="Vivek", consignee_phone="516-305-9127").startswith(b"%PDF")
    assert seen == ["ACME\n1 Main St\nContact: Vivek  Tel: 516-305-9127"]


def test_only_phone(monkeypatch):
    seen = _capture(monkeypatch)
    _build(consignee_phone="516-305-9127")
    assert seen == ["ACME\n1 Main St\nTel: 516-305-9127"]


def test_neither_leaves_card_unchanged(monkeypatch):
    seen = _capture(monkeypatch)
    _build(consignee_contact="  ", consignee_phone=None)
    assert seen == ["ACME\n1 Main St"]
