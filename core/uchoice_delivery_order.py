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

# Smallest font (px) the consignee box will shrink to before truncating.
MIN_WRAPPED_FONT_PX = 16
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
            # A single unbroken token wider than the box (CJK text has no
            # spaces; also very long tokens) is split per character.
            while stringWidth(current, font, font_pt) > max_width_pt and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and stringWidth(current[:cut], font, font_pt) > max_width_pt:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines


def _is_latin1(text: str) -> bool:
    try:
        text.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _draw_wrapped_top(c, text, x_px, y_px, w_px, h_px, font_px, tail=""):
    """Left-aligned, top-anchored, word-wrapped — matches the spec's TO/consignee alignment."""
    x_pt, top_pt, w_pt, h_pt = _rect_to_pt(x_px, y_px, w_px, h_px)
    text = text.strip()
    tail = (tail or "").strip()
    # ``tail`` (the contact/phone line) is always kept: if the text still
    # overflows at the smallest font, the company/address part is cut with
    # an ellipsis instead of losing the tail.
    full = f"{text}\n{tail}" if tail else text
    # Helvetica has no CJK glyphs: fall back to the CJK font when the text
    # needs it (company / address / contact may be Chinese).
    font = _FONT if _is_latin1(full) else _FONT_CJK
    # Shrink the font (down to a legible floor) until every line fits, so a
    # long company+address+contact never silently loses its trailing lines
    # (the phone number is the last line).
    font_pt = _px(font_px)
    min_pt = _px(MIN_WRAPPED_FONT_PX)
    while True:
        lines = _wrap_text(full, font, font_pt, w_pt)
        if len(lines) * font_pt * 1.3 - (font_pt * 0.3) <= h_pt or font_pt <= min_pt:
            break
        font_pt = max(min_pt, font_pt - _px(1))
    capacity = int((h_pt - font_pt) // (font_pt * 1.3)) + 1
    if len(lines) > capacity and tail:
        tail_lines = _wrap_text(tail, font, font_pt, w_pt)
        keep = max(capacity - len(tail_lines), 1)
        head = _wrap_text(text, font, font_pt, w_pt)[:keep]
        last = head[-1].rstrip()
        while last and stringWidth(last + "...", font, font_pt) > w_pt:
            last = last[:-1]
        head[-1] = last + "..."
        lines = head + tail_lines
    c.setFont(font, font_pt)
    line_height = font_pt * 1.3
    y = top_pt - font_pt
    for line in lines:
        if y < top_pt - h_pt:
            break
        c.drawString(x_pt, y, line)
        y -= line_height


COMPANY_TITLE = "TRANS WORLD FREIGHT SYSTEM CORP."


def _fmt_phone(phone) -> str:
    """A bare 10-digit number -> 123-456-7890; anything else is printed as stored."""
    raw = (phone or "").strip()
    if len(raw) == 10 and raw.isdigit():
        return f"{raw[:3]}-{raw[3:6]}-{raw[6:]}"
    return raw


def _sender_font(text: str, bold: bool) -> str:
    """Helvetica has no CJK glyphs: sender text (warehouse rows are free text)
    falls back to the CJK font per line, like the consignee box does."""
    if not _is_latin1(text):
        return _FONT_CJK
    return _FONT_BOLD if bold else _FONT


def _fit_block(texts, *, bold, sizes, max_w, max_lines_for):
    """Largest font size at which ALL text fits: each text is word-wrapped
    (per character for over-wide tokens) to ``max_w`` and the total line count
    stays within ``max_lines_for(size)``. Returns (size, [(line, font), ...]).

    Raises ValueError if even the smallest size does not fit: a long sender is
    never drawn past the page edge and never truncated, the PDF fails closed."""
    texts = [t for t in texts if t]
    for size in sizes:
        lines = []
        for t in texts:
            font = _sender_font(t, bold)
            lines.extend((ln, font) for ln in _wrap_text(t, font, size, max_w))
        if len(lines) <= max_lines_for(size):
            return size, lines
    raise ValueError("sender warehouse text is too long to print on the delivery order")


_HEADER_W = PAGE_W - 2 * 28 - 24
_FROM_W = PAGE_W - 28 - 6 - _px(900)
_NAME_W = PAGE_W - 28 - 6 - _px(120)


def sender_lines(sender) -> dict:
    """Printable, already laid-out pieces of the shipping-origin warehouse
    (company_warehouse row): header address block, FROM block, signature name.

    Raises ValueError if the sender is missing the address, or its text cannot
    be laid out within the page -- the caller must never fall back to another
    warehouse's address, and a long value must never run off the page."""
    if sender is None:
        raise ValueError("sender warehouse is required")
    addr = (getattr(sender, "addr", "") or "").strip()
    city = (getattr(sender, "city", "") or "").strip()
    state = (getattr(sender, "state", "") or "").strip()
    zip_code = (getattr(sender, "zip_code", "") or "").strip()
    if not (addr and city and state and zip_code):
        raise ValueError(f"sender warehouse {getattr(sender, 'warehouse_abbr', '?')} has an incomplete address")
    phone = _fmt_phone(getattr(sender, "phone", None))
    contact = (getattr(sender, "contact", "") or "").strip().upper()
    addr_u = addr.upper()
    comma = f"{city}, {state} {zip_code}".upper()
    plain = f"{city} {state} {zip_code}".upper()
    tel = f"TEL: {phone}" if phone else ""

    # Header: 10pt down to 7pt; room for (1 + 36/(1.4*size)) lines above the title.
    h_size, h_lines = _fit_block(
        [addr_u, comma, tel], bold=False, sizes=(10, 9, 8, 7.5, 7), max_w=_HEADER_W,
        max_lines_for=lambda sz: int(1 + 36 / (sz * 1.4)))
    # FROM block: company, address, city; the box has room down to the rule below it.
    f_size, f_lines = _fit_block(
        [COMPANY_TITLE.rstrip("."), addr_u, plain], bold=True, sizes=(9, 8.5, 8, 7.5, 7, 6.5), max_w=_FROM_W,
        max_lines_for=lambda sz: int(1 + 81 / (sz * 1.36)))
    n_size, n_lines = (None, [])
    if contact:
        n_size, n_lines = _fit_block(
            [contact], bold=False, sizes=(9, 8, 7), max_w=_NAME_W,
            max_lines_for=lambda sz: int(1 + 25 / (sz * 1.25)))
    return {
        "addr": addr_u, "city_line_comma": comma, "city_line": plain, "tel": tel, "contact": contact,
        "header": (h_size, h_lines), "from": (f_size, f_lines), "name": (n_size, n_lines),
    }


def _draw_static_background(c, outbound_order_no: str, sender: dict) -> None:
    """Everything that repeats on every page — header, labels, legal text —
    reconstructed from the xlsx master's actual text content. The header
    address and the FROM block come from the shipping-origin warehouse
    (``sender``, see sender_lines); the company title is the same for all."""
    cx = PAGE_W / 2

    # outer border
    margin = 28
    c.setLineWidth(1.2)
    c.rect(margin, margin, PAGE_W - 2 * margin, PAGE_H - 2 * margin)

    # header
    c.setFont(_FONT_BOLD, 16)
    c.drawCentredString(cx, PAGE_H - 60, COMPANY_TITLE)
    c.setFont(_FONT, 10)
    h_size, h_lines = sender["header"]
    for i, (line, font) in enumerate(h_lines):
        c.setFont(font, h_size)
        c.drawCentredString(cx, PAGE_H - (76 + h_size * 1.4 * i), line)

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
    f_size, f_lines = sender["from"]
    for i, (line, font) in enumerate(f_lines):
        c.setFont(font, f_size)
        c.drawString(_px(900), PAGE_H - _px(645) - f_size * 1.36 * i, line)

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
    n_size, n_lines = sender["name"]
    # Warehouse contact person (was a hard-coded "JEFF" for JFK).
    for i, (line, font) in enumerate(n_lines):
        c.setFont(font, n_size)
        c.drawString(_px(120), PAGE_H - _px(2030) - n_size * 1.25 * i, line)

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
    consignee_contact: str = "",
    consignee_phone: str = "",
    *,
    sender=None,
) -> bytes:
    """
    sender: the shipping-origin company_warehouse record (core.warehouse_directory
    WarehouseRecord or any object with addr/city/state/zip_code/phone/contact).
    REQUIRED -- raises ValueError rather than print another warehouse's address.

    product_lines: [{"description": str, "quantity_text": str}, ...] — already
    formatted per the pallet/plain rules by the caller (core/result_message.py
    style separation: this module only renders, callers decide the text).
    Paginates at MAX_ROWS_PER_PAGE; zero lines still produces one page.
    """
    sender_info = sender_lines(sender)
    buf = io.BytesIO()
    # ``invariant=1`` removes wall-clock metadata and random document IDs.
    # Durable Kefu delivery stores only a stable artifact reference + hash,
    # then regenerates bytes after a closed-window deferral; regeneration
    # therefore must be byte-identical, not merely visually identical.
    c = pdf_canvas.Canvas(buf, pagesize=letter, invariant=1)

    consignee_tail = ""
    consignee_text = consignee_company.strip()
    if consignee_addr.strip():
        consignee_text += f"\n{consignee_addr.strip()}"
    # Optional receiver contact line (both parts optional; omitted entirely when empty).
    contact_parts = []
    if (consignee_contact or "").strip():
        contact_parts.append(f"Contact: {consignee_contact.strip()}")
    if (consignee_phone or "").strip():
        contact_parts.append(f"Tel: {consignee_phone.strip()}")
    if contact_parts:
        consignee_tail = "  ".join(contact_parts)
    date_str = delivery_date.strftime("%m/%d/%Y")
    remark_text = f"Outbound Order #: {outbound_order_no}"

    pages = [product_lines[i:i + MAX_ROWS_PER_PAGE] for i in range(0, len(product_lines), MAX_ROWS_PER_PAGE)] or [[]]

    for page_lines in pages:
        _draw_static_background(c, outbound_order_no, sender_info)
        _draw_vcentered(c, date_str, 1090, 465, 300, 45, 27)
        _draw_wrapped_top(c, consignee_text, 205, 620, 600, 245, 27, tail=consignee_tail)
        for row_y, line in zip(_ROW_Y_PX, page_lines):
            _draw_vcentered(c, line["description"], 165, row_y, 545, 47, 25)
            _draw_vcentered(c, line["quantity_text"], 880, row_y, 535, 47, 25)
        _draw_vcentered(c, remark_text, 275, 1368, 650, 50, 24)
        c.showPage()

    c.save()
    return buf.getvalue()
