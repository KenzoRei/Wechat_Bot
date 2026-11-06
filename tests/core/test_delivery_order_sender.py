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


# ---- Codex audit of 9f17f98 ------------------------------------------------
def _sender(**kw):
    base = dict(warehouse_abbr="X", addr="1 A St", city="Gardena", state="CA", zip_code="90248",
                contact="Paul", phone="6262425505")
    base.update(kw)
    return SimpleNamespace(**base)


def _build(sender):
    return d.build_delivery_order_pdf(
        consignee_company="ACME", consignee_addr="1 Main St", delivery_date=datetime.date(2026, 11, 5),
        product_lines=[], outbound_order_no="OB-1", sender=sender)


def test_delivery_order_bytes_are_stored_at_enqueue_not_rebuilt():
    """P1: the PDF depends on a mutable warehouse row, so a rebuilt copy can differ
    from the queued one (artifact_hash_mismatch). The exact bytes must be stored."""
    from core.kefu_delivery import STORED_ARTIFACT_DOC_TYPES, content_hash
    assert "outbound_instruction" in STORED_ARTIFACT_DOC_TYPES
    assert content_hash(_build(_sender())) == content_hash(_build(_sender()))                    # stable if unchanged
    assert content_hash(_build(_sender())) != content_hash(_build(_sender(contact="Someone")))   # why storing matters


def test_chinese_sender_text_uses_the_cjk_font_on_every_sender_line():
    info = d.sender_lines(_sender(addr="加迪纳 红滩大道 293号", contact="张三"))
    for key in ("header", "from", "name"):
        for text, font in info[key][1]:
            assert font == (d._FONT_CJK if not d._is_latin1(text) else font)
            if not d._is_latin1(text):
                assert font == d._FONT_CJK
    assert ("张三", d._FONT_CJK) in info["name"][1]
    assert any(font == d._FONT_CJK for _t, font in info["header"][1])
    c = _Canvas()
    d._draw_static_background(c, "OB-1", info)
    assert "张三" in c.drawn
    # an all-ASCII line keeps Helvetica
    assert info["header"][1][-1][1] == d._FONT  # TEL line


class _MeasureCanvas(_Canvas):
    """Real font metrics: records (left x, width, y) of everything drawn."""
    def __init__(self):
        super().__init__()
        self.font = None
        self.spans = []

    def setFont(self, name, size):
        self.font = (name, size)

    def drawString(self, x, y, text):
        self.spans.append((x, d.stringWidth(text, *self.font), y))

    def drawCentredString(self, x, y, text):
        w = d.stringWidth(text, *self.font)
        self.spans.append((x - w / 2, w, y))


def test_long_addresses_are_wrapped_inside_the_page_with_nothing_lost():
    addr = "1234 Industrial Distribution Parkway, Building C, Loading Dock 7, Mid-Atlantic Freight Terminal Annex"
    info = d.sender_lines(_sender(addr=addr))
    c = _MeasureCanvas()
    d._draw_static_background(c, "OB-1", info)
    for x, w, _y in c.spans:
        assert x >= 28 and x + w <= d.PAGE_W - 28, (x, w)       # inside the page border
    for key in ("header", "from"):
        printed = " ".join(t for t, _f in info[key][1])
        for word in addr.upper().split():
            assert word in printed                                # whole address preserved
    # the header stays above the PICKUP / DELIVERY ORDER title (baseline 132pt from top)
    header_baselines = [d.PAGE_H - y for _x, _w, y in c.spans if 70 < d.PAGE_H - y < 125]
    assert header_baselines and max(header_baselines) < 118


def test_unprintably_long_sender_fails_closed_instead_of_overflowing():
    with pytest.raises(ValueError, match="too long"):
        d.sender_lines(_sender(addr="W" * 300))
    with pytest.raises(ValueError):
        _build(_sender(addr="W" * 300))
    # the longest contact the table allows (200 chars) still fits, wrapped, inside the page
    info = d.sender_lines(_sender(contact="W" * 200))
    c = _MeasureCanvas()
    d._draw_static_background(c, "OB-1", info)
    assert all(x >= 28 and x + w <= d.PAGE_W - 28 for x, w, _y in c.spans)
    assert "".join(t for t, _f in info["name"][1]) == "W" * 200


def test_cutover_check_script_targets_pending_delivery_orders_without_blobs():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_delivery_order_cutover.py").read_text(encoding="utf-8")
    assert "artifact_doc_type = 'outbound_instruction'" in src
    assert "NOT EXISTS (SELECT 1 FROM kefu_artifact_blob" in src
    assert "set_session(readonly=True)" in src
