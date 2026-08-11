"""
Delivery order PDF generation for confirm_outbound_completion.

Reproduces the TWF "Pickup / Delivery Order" document (see the LLM handoff
spec supplied for this feature) without needing the original PNG background
or PyQt6 — we have no background image asset, only the editable xlsx master
and a rendered sample, so this draws the whole page as vectors/text with
reportlab. The five dynamic field positions below are taken directly from
the approved coordinate spec (px on a 1700x2200 canvas @ 200 DPI, which maps
exactly onto a 612x792pt US Letter page — 72/200 = 0.36, so px * 0.36 = pt).
Everything else (headers, labels, legal text) is reconstructed from the
xlsx's actual static text so wording matches exactly; its exact position is
our own layout, not extracted from the source, since the spec doesn't cover it.
"""
import io
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.pdfbase.pdfmetrics import stringWidth, registerFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

PAGE_W, PAGE_H = letter  # 612 x 792 pt
_SCALE = 0.36  # 72/200 — px (on the approved 1700x2200 canvas) -> pt

MAX_ROWS_PER_PAGE = 8
_ROW_Y_PX = [882, 930, 978, 1026, 1074, 1122, 1170, 1218]  # approved, includes the 10px offset

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
# Helvetica has no CJK glyphs — the signature-block labels are bilingual
# (e.g. "SIGNATURE（签名）："), so those specifically need a CJK-capable font.
# STSong-Light is one of reportlab's built-in CID fonts — no external font
# file needed.
_FONT_CJK = "STSong-Light"
registerFont(UnicodeCIDFont(_FONT_CJK))


def _px(v: float) -> float:
    return v * _SCALE


def _rect_to_pt(x_px, y_px, w_px, h_px):
    """MD coords are (x, y, width, height) top-left-anchored on the px canvas.
    Returns (x_pt, top_y_pt, w_pt, h_pt) in reportlab's bottom-left-origin space."""
    x_pt = _px(x_px)
    w_pt = _px(w_px)
    h_pt = _px(h_px)
    top_y_pt = PAGE_H - _px(y_px)
    return x_pt, top_y_pt, w_pt, h_pt


def _draw_vcentered(c, text, x_px, y_px, w_px, h_px, font_px, bold=False):
    """Left-aligned, vertically centered within the rect — matches the spec's
    alignment for delivery date / product description / product quantity /
    outbound order number."""
    x_pt, top_pt, w_pt, h_pt = _rect_to_pt(x_px, y_px, w_px, h_px)
    font_pt = _px(font_px)
    font = _FONT_BOLD if bold else _FONT
    c.setFont(font, font_pt)
    baseline_y = top_pt - h_pt / 2 - font_pt * 0.35
    c.drawString(x_pt, baseline_y, text)


def _wrap_text(text: str, font: str, font_pt: float, max_width_pt: float) -> list[str]:
    """Word-wraps each explicit '\\n'-separated segment independently, so a
    forced line break (e.g. between consignee company and address) survives."""
    lines: list[str] = []
    for segment in text.split("\n"):
        words = segment.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if stringWidth(candidate, font, font_pt) <= max_width_pt or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_wrapped_top(c, text, x_px, y_px, w_px, h_px, font_px):
    """Left-aligned, top-anchored, word-wrapped — matches the spec's TO/consignee alignment."""
    x_pt, top_pt, w_pt, h_pt = _rect_to_pt(x_px, y_px, w_px, h_px)
    font_pt = _px(font_px)
    c.setFont(_FONT, font_pt)
    line_height = font_pt * 1.3
    y = top_pt - font_pt
    for line in _wrap_text(text.strip(), _FONT, font_pt, w_pt):
        if y < top_pt - h_pt:
            break
        c.drawString(x_pt, y, line)
        y -= line_height


def _draw_static_background(c, outbound_order_no: str) -> None:
    """Everything that repeats on every page — header, labels, legal text —
    reconstructed from the xlsx master's actual text content."""
    cx = PAGE_W / 2

    # outer border
    margin = 28
    c.setLineWidth(1.2)
    c.rect(margin, margin, PAGE_W - 2 * margin, PAGE_H - 2 * margin)

    # header
    c.setFont(_FONT_BOLD, 16)
    c.drawCentredString(cx, PAGE_H - 60, "TRANS WORLD FREIGHT SYSTEM CORP.")
    c.setFont(_FONT, 10)
    c.drawCentredString(cx, PAGE_H - 76, "145-02 156TH STREET")
    c.drawCentredString(cx, PAGE_H - 90, "JAMAICA, NY 11434")
    c.drawCentredString(cx, PAGE_H - 104, "TEL: 718-276-6169   FAX: 646-243-5122")

    c.setFont(_FONT_BOLD, 15)
    c.drawCentredString(cx, PAGE_H - 132, "PICKUP / DELIVERY ORDER")

    # date row
    c.setFont(_FONT_BOLD, 9)
    c.drawString(_px(700), PAGE_H - _px(490), "DATE OF Delivery:")
    c.line(margin, PAGE_H - _px(510), PAGE_W - margin, PAGE_H - _px(510))

    # TO / FROM labels + FROM static block
    c.setFont(_FONT_BOLD, 10)
    c.drawString(_px(205), PAGE_H - _px(600), "TO")
    c.drawString(_px(900), PAGE_H - _px(600), "FROM")
    c.setFont(_FONT_BOLD, 9)
    for i, line in enumerate([
        "TRANS WORLD FREIGHT SYSTEM CORP",
        "145-02 156TH STREET",
        "JAMAICA NY 11434",
    ]):
        c.drawString(_px(900), PAGE_H - _px(645 + i * 34), line)

    # TO box border
    x_pt, top_pt, w_pt, h_pt = _rect_to_pt(195, 615, 620, 255)
    c.rect(x_pt, top_pt - h_pt, w_pt, h_pt)

    c.line(margin, PAGE_H - _px(880), PAGE_W - margin, PAGE_H - _px(880))

    # product row underlines
    for row_y in _ROW_Y_PX:
        line_y = PAGE_H - _px(row_y + 47)
        c.line(_px(165), line_y, PAGE_W - margin, line_y)

    c.line(margin, PAGE_H - _px(1275), PAGE_W - margin, PAGE_H - _px(1275))

    # REMARK label + legal text + JEFF (bottom-left)
    c.setFont(_FONT_BOLD, 10)
    c.drawString(_px(100), PAGE_H - _px(1330), "REMARK:")
    c.setFont(_FONT, 6.5)
    for i, line in enumerate([
        "LIABILITY INCLUDING NEGLIGENCE IS LIMITED TO THE SUM OF $50.00",
        "PER SHIPMENT, UNLESS A GREATER VALUATION SHALL BE PAID FOR OR",
        "AGREED TO BE PAID IN WRITING PRIOR TO SHIPPING.",
    ]):
        c.drawString(_px(100), PAGE_H - _px(1930 + i * 28), line)
    c.setFont(_FONT, 9)
    c.drawString(_px(120), PAGE_H - _px(2030), "JEFF")

    # signature block (right side)
    labels = [
        ("SIGNATURE（签名）：", 1330),
        ("PRINTED LAST NAME（正楷签名）：", 1460),
        ("DATE（日期）：", 1590),
        ("TIME（时间）：", 1720),
    ]
    for text, y_px in labels:
        c.setFont(_FONT_CJK, 9)
        c.drawString(_px(1000), PAGE_H - _px(y_px), text)
        c.line(_px(1000), PAGE_H - _px(y_px + 55), PAGE_W - margin, PAGE_H - _px(y_px + 55))


def build_delivery_order_pdf(
    consignee_company: str,
    consignee_addr: str,
    delivery_date: date,
    product_lines: list[dict],
    outbound_order_no: str,
) -> bytes:
    """
    product_lines: [{"description": str, "quantity_text": str}, ...] — already
    formatted per the pallet/plain rules by the caller (core/result_message.py
    style separation: this module only renders, callers decide the text).
    Paginates at MAX_ROWS_PER_PAGE; zero lines still produces one page.
    """
    buf = io.BytesIO()
    # ``invariant=1`` removes wall-clock metadata and random document IDs.
    # Durable Kefu delivery stores only a stable artifact reference + hash,
    # then regenerates bytes after a closed-window deferral; regeneration
    # therefore must be byte-identical, not merely visually identical.
    c = pdf_canvas.Canvas(buf, pagesize=letter, invariant=1)

    consignee_text = consignee_company.strip()
    if consignee_addr.strip():
        consignee_text += f"\n{consignee_addr.strip()}"
    date_str = delivery_date.strftime("%m/%d/%Y")
    remark_text = f"Outbound Order #: {outbound_order_no}"

    pages = [product_lines[i:i + MAX_ROWS_PER_PAGE] for i in range(0, len(product_lines), MAX_ROWS_PER_PAGE)] or [[]]

    for page_lines in pages:
        _draw_static_background(c, outbound_order_no)
        _draw_vcentered(c, date_str, 1090, 465, 300, 45, 27)
        _draw_wrapped_top(c, consignee_text, 205, 620, 600, 245, 27)
        for row_y, line in zip(_ROW_Y_PX, page_lines):
            _draw_vcentered(c, line["description"], 165, row_y, 545, 47, 25)
            _draw_vcentered(c, line["quantity_text"], 880, row_y, 535, 47, 25)
        _draw_vcentered(c, remark_text, 275, 1368, 650, 50, 24)
        c.showPage()

    c.save()
    return buf.getvalue()
