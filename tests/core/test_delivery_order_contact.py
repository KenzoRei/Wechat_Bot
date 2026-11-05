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


class _FakeCanvas:
    def __init__(self):
        self.fonts, self.drawn = [], []

    def setFont(self, name, size):
        self.fonts.append((name, size))

    def drawString(self, x, y, text):
        self.drawn.append(text)


def _draw(text):
    c = _FakeCanvas()
    d._draw_wrapped_top(c, text, 205, 620, 600, 245, 27)
    return c


def test_long_text_shrinks_instead_of_dropping_phone():
    long_text = (
        "Transworld Distribution Services International LLC\n"
        "1309 East Bay Avenue Unit 1309B Building C, South Bronx, New York 10474-1234 "
        "Loading Dock 4 Rear Entrance Gate B, Receiving Office Suite 200, Attn Night Shift Supervisor, "
        "Call before arrival and wait for the gate guard to open the rear gate\nContact: Vivek Ramaswamy-Narayanan  Tel: 516-305-9127"
    )
    c = _draw(long_text)
    assert c.drawn[-1].endswith("516-305-9127")
    assert c.fonts[-1][1] < d._px(27)


def test_chinese_text_uses_cjk_font_and_keeps_phone():
    c = _draw("复兴国际物流有限公司\n上海市浦东新区世纪大道一百号国际金融中心A座二十八层\nContact: 张三  Tel: 138-0000-0000")
    assert c.fonts[-1][0] == d._FONT_CJK
    assert "".join(c.drawn).endswith("138-0000-0000")


def test_latin_text_keeps_helvetica_and_full_size():
    c = _draw("ACME\n1 Main St\nContact: Vivek  Tel: 516-305-9127")
    assert c.fonts[-1] == (d._FONT, d._px(27))
