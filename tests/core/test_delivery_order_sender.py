"""Delivery order header / FROM block / signature name come from the origin warehouse (no DB)."""
import datetime
from types import SimpleNamespace

import pytest

from core import uchoice_delivery_order as d

WAREHOUSES = {
    "JFK": SimpleNamespace(warehouse_abbr="JFK", addr="145-02 156th St", city="Jamaica", state="NY",
                           zip_code="11434", contact="Jeff", phone="6462435122"),
    "DE": SimpleNamespace(warehouse_abbr="DE", addr="201 Gabor Dr", city="Newark", state="DE",
                          zip_code="19711", contact="Zorro Zhang", phone="3472040602"),
    "NJ": SimpleNamespace(warehouse_abbr="NJ", addr="120 Raskulinecz Rd", city="Carteret", state="NJ",
                          zip_code="07008", contact="Kevin", phone="3478117879"),
}


class _Canvas:
    """Records everything drawn; ignores the rest of the canvas API."""
    def __init__(self):
        self.drawn = []

    def drawString(self, x, y, text):
        self.drawn.append(text)

    def drawCentredString(self, x, y, text):
        self.drawn.append(text)

    def __getattr__(self, name):
        return lambda *a, **k: None


def _drawn(code):
    c = _Canvas()
    d._draw_static_background(c, "OB-1", d.sender_lines(WAREHOUSES[code]))
    return c.drawn


@pytest.mark.parametrize("code,addr,city,tel,contact", [
    ("JFK", "145-02 156TH ST", "JAMAICA, NY 11434", "TEL: 646-243-5122", "JEFF"),
    ("DE", "201 GABOR DR", "NEWARK, DE 19711", "TEL: 347-204-0602", "ZORRO ZHANG"),
    ("NJ", "120 RASKULINECZ RD", "CARTERET, NJ 07008", "TEL: 347-811-7879", "KEVIN"),
])
def test_each_warehouse_prints_its_own_block(code, addr, city, tel, contact):
    out = _drawn(code)
    assert out.count(addr) == 2                      # header + FROM block
    assert city in out and city.replace(",", "") in out
    assert tel in out and contact in out
    assert d.COMPANY_TITLE in out and "TRANS WORLD FREIGHT SYSTEM CORP" in out
    assert not any("FAX" in t for t in out)


@pytest.mark.parametrize("code", ["DE", "NJ"])
def test_other_warehouses_never_print_the_jfk_sender(code):
    text = " | ".join(_drawn(code))
    for jfk in ("145-02", "156TH", "JAMAICA", "11434", "718-276-6169", "646-243-5122", "JEFF"):
        assert jfk not in text


def test_missing_phone_and_contact_are_omitted():
    sender = SimpleNamespace(warehouse_abbr="X", addr="1 A St", city="B", state="CA", zip_code="90001",
                             contact=None, phone=None)
    c = _Canvas()
    d._draw_static_background(c, "OB-1", d.sender_lines(sender))
    assert not any(t.startswith("TEL") for t in c.drawn)


def test_missing_or_incomplete_sender_fails_instead_of_defaulting():
    common = dict(consignee_company="ACME", consignee_addr="1 Main St",
                  delivery_date=datetime.date(2026, 11, 5), product_lines=[], outbound_order_no="OB-1")
    with pytest.raises(ValueError):
        d.build_delivery_order_pdf(**common)
    with pytest.raises(ValueError):
        d.build_delivery_order_pdf(**common, sender=SimpleNamespace(addr="", city="", state="", zip_code=""))


def test_full_pdf_builds_for_every_warehouse():
    for sender in WAREHOUSES.values():
        pdf = d.build_delivery_order_pdf(
            consignee_company="ACME", consignee_addr="1 Main St", delivery_date=datetime.date(2026, 11, 5),
            product_lines=[{"description": "Item", "quantity_text": "Quantity: 1"}],
            outbound_order_no="OB-1", sender=sender)
        assert pdf.startswith(b"%PDF")


def test_pdf_stub_skips_when_origin_warehouse_unknown(monkeypatch, capsys):
    from handlers.uchoice import pdf_stub
    from core import warehouse_directory
    monkeypatch.setattr(warehouse_directory, "get_warehouse", lambda db, code: None)
    assert pdf_stub._load_sender(object(), "ZZZ", "S1") is None
    assert pdf_stub._load_sender(object(), None, "S1") is None
    assert "skipped" in capsys.readouterr().out
