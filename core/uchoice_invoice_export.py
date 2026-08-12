"""
Excel export of the detail data behind an invoice — one row per contributing
transaction, not just the aggregated totals compute_invoice() returns. Uses
the exact same row-selection helpers as compute_invoice() (core/uchoice_invoice.py)
so the workbook and the chat summary can never silently drift apart.
"""
import io
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session as DBSession
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

from core.uchoice_invoice import compute_invoice, _resolve_range, _completed_logs, _ledger_rows
from core.uchoice_context import sku_label_map, get_original_fields

_HEADER_FONT = Font(bold=True)


def _write_header(ws, row: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left")


def _autosize(ws) -> None:
    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(length + 2, 10), 60)


def build_invoice_workbook(db: DBSession, warehouse_code: str, start_month: str, end_month: str | None = None) -> bytes:
    end_month = end_month or start_month
    start, end, end_exclusive = _resolve_range(start_month, end_month)
    summary = compute_invoice(db, warehouse_code, start_month, end_month)
    sku_labels = sku_label_map(db)

    wb = Workbook()

    # ── Summary ──────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Warehouse", warehouse_code])
    ws.append(["Range", f"{start_month} to {end_month}"])
    ws.append(["Generated at (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    ws.append([])
    _write_header(ws, ws.max_row + 1, ["Charge", "Amount (USD)"])
    ws.append(["Transportation fee", float(summary["transportation_fee"])])
    ws.append(["Palletization fee", float(summary["palletization_fee"])])
    ws.append(["Unpacking fee", float(summary["unpacking_fee"])])
    ws.append(["Storage fee", float(summary["storage_fee"])])
    total_row = ws.max_row + 1
    ws.append(["Total", float(summary["total"])])
    ws.cell(row=total_row, column=1).font = _HEADER_FONT
    ws.cell(row=total_row, column=2).font = _HEADER_FONT
    _autosize(ws)

    # ── Transportation & Palletization (outbound completions) ──────────────
    from models.uchoice import UchoiceAddress

    ws2 = wb.create_sheet("Transportation & Palletization")
    _write_header(ws2, 1, [
        "Serial Number", "Completed At (UTC)", "SKU Lines",
        "Destination Company", "Destination Address",
        "Transportation Fee", "Palletization Fee",
    ])
    outbound_logs = _completed_logs(db, "uchoice_outbound_request", warehouse_code, start, end_exclusive)
    for log in outbound_logs:
        result = log.result or {}
        lines = result.get("fulfillment_lines") or []
        sku_summary = "; ".join(
            f"{sku_labels.get(l.get('sku_code'), l.get('sku_code', '?'))} x{l.get('pallet_count', l.get('box_count', '?'))}"
            for l in lines
        )

        # destination isn't in result — it's on the original request, not the
        # completion's own fields, so it's resolved the same way the
        # confirmation/response builders do (core/uchoice_context.py).
        destination_company = ""
        destination_addr = ""
        original_fields = get_original_fields(db, log)
        destination_address_id = original_fields.get("destination_address_id")
        if destination_address_id:
            addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
            if addr:
                destination_company = addr.company_name or ""
                destination_addr = addr.addr

        ws2.append([
            log.serial_number,
            log.completed_at.strftime("%Y-%m-%d %H:%M") if log.completed_at else "",
            sku_summary,
            destination_company,
            destination_addr,
            float(Decimal(str(result.get("transportation_fee", 0)))),
            float(Decimal(str(result.get("palletization_fee", 0)))),
        ])
    _autosize(ws2)

    # ── Unpacking (inbound completions) ─────────────────────────────────────
    ws3 = wb.create_sheet("Unpacking")
    _write_header(ws3, 1, ["Serial Number", "Completed At (UTC)", "SKU Lines", "Unpacking Fee"])
    inbound_logs = _completed_logs(db, "uchoice_inbound_request", warehouse_code, start, end_exclusive)
    for log in inbound_logs:
        result = log.result or {}
        lines = result.get("received_lines") or []
        sku_summary = "; ".join(
            f"{sku_labels.get(l.get('sku_code'), l.get('sku_code', '?'))} x{l.get('pallet_count', l.get('box_count', '?'))}"
            for l in lines
        )
        ws3.append([
            log.serial_number,
            log.completed_at.strftime("%Y-%m-%d %H:%M") if log.completed_at else "",
            sku_summary,
            float(Decimal(str(result.get("unpacking_fee", 0)))),
        ])
    _autosize(ws3)

    # ── Storage (daily ledger) ───────────────────────────────────────────
    ws4 = wb.create_sheet("Storage")
    _write_header(ws4, 1, ["Date", "Pallet Count", "Storage Fee"])
    for row in _ledger_rows(db, warehouse_code, start, end):
        ws4.append([row.fee_date.isoformat(), row.pallet_count, float(row.storage_fee)])
    _autosize(ws4)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_invoice_artifact(
    db: DBSession, warehouse_code: str, start_month: str, end_month: str | None, request_log_id
) -> dict:
    """
    Channel-neutral artifact wrapper around build_invoice_workbook, matching
    the {bytes, filename, content_type, artifact_key} shape handlers/uchoice/
    pdf_stub.py's PDF artifacts use -- so Kefu delivery (core/kefu_delivery.py's
    enqueue_file) and its replay path (core/kefu_artifact_loader.py) can
    handle an invoice workbook exactly like any other durable Kefu file, no
    Excel-specific casing needed there. artifact_key is stable per (request,
    doc_type) for the same idempotent-regeneration reason PDFs use it.
    """
    end_month = end_month or start_month
    data = build_invoice_workbook(db, warehouse_code, start_month, end_month)
    return {
        "bytes": data,
        "filename": f"invoice_{warehouse_code}_{start_month}_{end_month}.xlsx",
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "artifact_key": f"{request_log_id}:invoice_workbook",
    }
