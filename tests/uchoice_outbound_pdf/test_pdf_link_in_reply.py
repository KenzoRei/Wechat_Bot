"""
The delivery-order PDF (generate_pdf_stub, doc_type=outbound_instruction) is
generated as part of uchoice_outbound_request's OWN workflow, at request
time (phase3-outbound-pdf-timing.md) -- but core/result_message.py's
RESULT_BUILDERS mapped this service to the shared _empty_sections_builder,
which unconditionally returns [] "nothing restated" (correct reasoning for
sku_lines/destination, which the confirm step already showed) -- except this
step computes a genuinely NEW pdf_url right here, in this same reply, that
was never shown before. The link was silently dropped from every outbound-
request confirmation reply on both channels (this handler is shared via
handlers/reply_wechat.py's HANDLER_REGISTRY dispatch), even though the PDF
itself built successfully every time. No DB access needed -- the builder is
a pure function of context.
"""
from core.result_message import RESULT_BUILDERS


def test_outbound_request_reply_includes_pdf_link_when_present():
    builder = RESULT_BUILDERS["uchoice_outbound_request"]
    context = {"result": {"pdf_url": "https://example.test/files/download/tok123"}}

    sections = builder(context, db=None)

    assert len(sections) == 1
    assert "https://example.test/files/download/tok123" in sections[0]["items"][0]
    assert "下载送货单" in sections[0]["items"][0]


def test_outbound_request_reply_has_no_sections_when_pdf_not_attempted():
    """No pdf_url and no failure recorded (e.g. 'pending'): nothing is shown, and
    never a broken/empty link."""
    builder = RESULT_BUILDERS["uchoice_outbound_request"]

    assert builder({"result": {"pdf_status": "pending", "pdf_url": None}}, db=None) == []
    assert builder({"result": {}}, db=None) == []
    assert builder({}, db=None) == []


def test_outbound_request_reply_says_so_when_the_pdf_failed():
    """The request succeeded but its delivery order was skipped/failed: the reply
    must say so (previously silent), without a link and without telling staff to
    resubmit."""
    from core.result_message import DELIVERY_ORDER_FAILED_NOTICE

    builder = RESULT_BUILDERS["uchoice_outbound_request"]
    sections = builder({"result": {"pdf_status": "failed", "pdf_url": None}}, db=None)

    assert sections == [{"label": None, "type": "raw", "items": [DELIVERY_ORDER_FAILED_NOTICE]}]
    assert "送货单未能生成" in DELIVERY_ORDER_FAILED_NOTICE and "勿重复提交" in DELIVERY_ORDER_FAILED_NOTICE
    assert "http" not in DELIVERY_ORDER_FAILED_NOTICE


def test_kefu_marks_the_pdf_failed_when_the_artifact_is_skipped():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "core" / "kefu_turn_apply.py").read_text(encoding="utf-8")
    assert 'context["result"]["pdf_status"] = "failed"' in src
