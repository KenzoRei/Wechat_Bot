"""
Shared byte-determinism fix for openpyxl-generated .xlsx exports. Any export
delivered through Kefu's durable delivery queue (core/kefu_delivery.py)
must be regenerable byte-for-byte identical, since that queue verifies a
content hash before every send and regenerates the artifact from scratch on
retry/redelivery rather than storing raw bytes -- confirmed live as the
root cause of every Kefu invoice export permanently failing before this fix
existed. Smart Robot's own export paths call the same builders but don't
need this guarantee (they send bytes once, synchronously, with no
re-verification step) -- passing generated_at=None there is harmless.
"""
import io
import re
import zipfile
from datetime import datetime

_CORE_XML_MODIFIED_RE = re.compile(
    rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)"
)


def freeze_xlsx_timestamps(data: bytes, generated_at: datetime) -> bytes:
    """
    openpyxl's Workbook.save() unconditionally re-stamps
    properties.modified with datetime.now() during the save itself --
    setting wb.properties.modified beforehand (at any point, including
    immediately before save()) has no effect, confirmed empirically.
    docProps/core.xml's <dcterms:modified> is the only content difference
    between two saves of an otherwise-identical workbook; every ZIP
    entry's own date_time metadata already matches at the granularity
    that matters here. Rewrite that one XML value post-save and re-zip
    with every entry's date_time pinned to generated_at, so two builds of
    the same underlying data are byte-identical regardless of when each
    was actually generated.
    """
    stamp = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ").encode("ascii")
    date_time = (generated_at.year, generated_at.month, generated_at.day,
                 generated_at.hour, generated_at.minute, generated_at.second)

    src = zipfile.ZipFile(io.BytesIO(data))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                content = _CORE_XML_MODIFIED_RE.sub(rb"\g<1>" + stamp + rb"\g<2>", content)
            new_info = zipfile.ZipInfo(info.filename, date_time=date_time)
            new_info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(new_info, content)
    return out_buf.getvalue()
