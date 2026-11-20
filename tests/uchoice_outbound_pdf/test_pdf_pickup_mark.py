"""
Customer pickup on the delivery-order PDF (core/uchoice_delivery_order.py) --
docs/ai-collaboration/2026-11-customer-pickup/plan.md, audit finding 8.

A pickup order gets a black REMARK-area bar and a CJK consignee block. A
request WITHOUT a stored pickup_type must take the exact existing drawing
path: Kefu regenerates outbound PDFs after a closed-window deferral and checks
the bytes against the hash that was enqueued, so the golden files below (made
from the builder BEFORE this feature existed, reportlab 5.1.0) must match
byte for byte.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from core import pickup
from core.uchoice_delivery_order import build_delivery_order_pdf

GOLDEN = Path(__file__).parent / "golden"
LINES2 = [{"description": "Jelly Cup Mix - Item S2", "quantity_text": "Quantity: 120"},
          {"description": "Rice Cracker - Item T1", "quantity_text": "1,200 Units per Plt x 2 Plts = 2,400"}]
LINES9 = [{"description": f"Item {i}", "quantity_text": f"Quantity: {i}"} for i in range(1, 10)]


def _build(lines, order_no, company="ABC Trading Co", addr="123 Main St, Queens, NY 11101", **kwargs):
    return build_delivery_order_pdf(company, addr, date(2026, 11, 20), lines, order_no, **kwargs)


# ── byte identity for everything that is not a stored pickup ──────────────────

def test_a_request_with_no_pickup_type_is_byte_identical_to_the_pre_feature_output():
    golden = (GOLDEN / "no_pickup_1page.pdf").read_bytes()
    assert _build(LINES2, "REQ-20261120-000142") == golden, (
        "the no-pickup drawing path changed; if reportlab was upgraded, regenerate the golden files "
        "from the PREVIOUS builder and confirm Kefu's deferred-delivery hash check still holds"
    )


def test_a_multi_page_request_with_no_pickup_type_is_byte_identical():
    golden = (GOLDEN / "no_pickup_2pages.pdf").read_bytes()
    assert _build(LINES9, "REQ-20261120-000143") == golden


def test_pdf_args_are_empty_for_old_and_ordinary_requests():
    assert pickup.pdf_args({}) == {}
    assert pickup.pdf_args({"destination_address_id": "x", "warehouse_code": "JFK"}) == {}
    assert pickup.pdf_args({"pickup_type": "none"}) == {}
    assert _build(LINES2, "REQ-20261120-000142", **pickup.pdf_args({"warehouse_code": "JFK"})) == \
        (GOLDEN / "no_pickup_1page.pdf").read_bytes()


def test_pdf_args_for_a_pickup_carry_the_stored_type_and_name():
    assert pickup.pdf_args({"pickup_type": "customer", "pickup_customer_name": "王小明"}) == \
        {"pickup_type": "customer", "pickup_customer_name": "王小明"}
    assert pickup.pdf_args({"pickup_type": "retention", "pickup_customer_name": "张三"}) == \
        {"pickup_type": "retention", "pickup_customer_name": None}


# ── the pickup mark ───────────────────────────────────────────────────────────

def _page_texts(pdf_bytes):
    import pymupdf
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return [page.get_text() for page in doc], doc


@pytest.fixture(scope="module")
def pymupdf_available():
    pytest.importorskip("pymupdf")


def test_customer_pickup_pdf_carries_the_bar_and_the_name(pymupdf_available):
    pdf = _build(LINES2, "REQ-20261120-000142", company="客户自提", addr="",
                 pickup_type="customer", pickup_customer_name="王小明")
    texts, _ = _page_texts(pdf)
    assert "客户自提 — 请勿送货" in texts[0] and "客户自提：王小明" in texts[0] and "自提" in texts[0]


def test_retention_pdf_carries_its_own_bar_and_no_name(pymupdf_available):
    pdf = _build(LINES2, "REQ-20261120-000142", company="JFK仓库自提留存", addr="14502 156th St",
                 pickup_type="retention", pickup_customer_name=None)
    text = _page_texts(pdf)[0][0]
    assert "仓库自提留存 — 请勿送货" in text and "客户自提：" not in text
    assert "JFK仓库自提留存" in text            # the consignee prints (it used to be black boxes)


def test_the_bar_repeats_on_every_page_of_a_multi_page_pickup_order(pymupdf_available):
    pdf = _build(LINES9, "REQ-20261120-000143", company="客户自提", addr="",
                 pickup_type="customer", pickup_customer_name="王小明")
    texts, doc = _page_texts(pdf)
    assert len(texts) == 2
    assert all("客户自提 — 请勿送货" in t and "客户自提：王小明" in t for t in texts)


def test_the_bar_does_not_overlap_the_order_number_or_the_signature_column(pymupdf_available):
    import pymupdf
    pdf = _build(LINES2, "REQ-20261120-000142", company="客户自提", addr="",
                 pickup_type="retention")
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    bar = page.search_for("仓库自提留存 — 请勿送货")[0]
    order_no = page.search_for("Outbound Order #")[0]
    signature = page.search_for("SIGNATURE")[0]
    assert bar.y0 > order_no.y1                 # under the order number
    assert bar.x1 < signature.x0                # left of the signature column


def test_a_long_pickup_name_wraps_inside_the_consignee_box(pymupdf_available):
    import pymupdf
    name = "王" * 40
    pdf = _build(LINES2, "REQ-20261120-000142", company="客户自提", addr="",
                 pickup_type="customer", pickup_customer_name=name)
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    box_right = 195 * 0.36 + 620 * 0.36          # the TO box on the 1700x2200 canvas
    words = page.get_text("words")
    name_words = [w for w in words if "王" in w[4]]
    assert name_words and all(w[2] <= box_right + 1 for w in name_words)
    assert re.sub(r"\s", "", " ".join(w[4] for w in name_words)).count("王") == 40
