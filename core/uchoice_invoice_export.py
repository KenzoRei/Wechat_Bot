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

from core.uchoice_invoice import (
    compute_combined_invoice, invoice_warehouse_list, select_invoice_rows, _resolve_range,
)
from core.uchoice_inventory import inventory_balances, format_closing_mix
from core.uchoice_context import sku_label_map, get_original_fields
from core.xlsx_determinism import freeze_xlsx_timestamps

_HEADER_FONT = Font(bold=True)


def _write_header(ws, row: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left")


def _autosize(ws) -> None:
    for col_cells in ws.columns:
        # Longest line, not longest string: a wrapped multi-line cell (the
        # Inventory sheet's Closing Detail) shouldn't widen its column.
        length = max(
            (max(len(line) for line in str(c.value).splitlines() or [""]) if c.value is not None else 0)
            for c in col_cells
        )
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(length + 2, 10), 60)


def _finish_detail_sheet(ws) -> None:
    """Header row stays visible while scrolling, with a filter on every column."""
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autosize(ws)


_INVENTORY_NOTES = (
    "Inbound / Outbound / Other count pallets. A loose-box pick doesn't change the pallet count; "
    "it shows in Closing Detail as a smaller pallet.",
    "Other Net Change = transfers between warehouses, internal moves/repackaging, adjustments and "
    "recounts. See the storage history export for each change.",
    "Balances are as recorded in the system.",
)


def _write_inventory_sheet(wb, balances: list[dict], sku_labels: dict) -> None:
    """
    Per-SKU opening/closing pallets for the period (core/uchoice_inventory.py),
    from the same balances the Summary and chat totals use.
    Closing is a formula so staff can see it add up; Closing Detail lists the
    closing balance per pallet size, one per line, like the 库存查询 reply.
    """
    ws = wb.create_sheet("Inventory")
    _write_header(ws, 1, [
        "Warehouse", "SKU", "Description", "Opening (plt)", "Closing (plt)",
        "Inbound (plt)", "Outbound (plt)", "Other Net Change (plt)", "Closing Detail",
    ])
    top = Alignment(vertical="top")
    wrap_top = Alignment(vertical="top", wrap_text=True)
    row = 2
    for b in balances:
        ws.append([
            b["warehouse_code"], b["sku_code"], sku_labels.get(b["sku_code"], ""),
            b["opening"], f"=D{row}+F{row}+G{row}+H{row}",
            b["inbound"], b["outbound"], b["other"],
            format_closing_mix(b["closing_mix"]),
        ])
        for col in range(1, 9):
            ws.cell(row=row, column=col).alignment = top
        ws.cell(row=row, column=9).alignment = wrap_top
        lines = len(b["closing_mix"])
        if lines > 1:
            ws.row_dimensions[row].height = 15 * lines
        row += 1
    last = row - 1

    total = ["Total", None, None]
    total += [f"=SUM({col}2:{col}{last})" if last >= 2 else 0 for col in "DEFGH"]
    ws.append(total + [None])
    for col in range(1, 10):
        ws.cell(row=row, column=col).font = _HEADER_FONT

    ws.append([None])  # spacer row (append([]) writes nothing)
    for note in _INVENTORY_NOTES:
        ws.append([note])
        ws.cell(row=ws.max_row, column=1).font = Font(italic=True)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{max(last, 1)}"
    _autosize(ws)
    # Notes are long single cells in column A; size A to the data, not them.
    ws.column_dimensions["A"].width = 12


def _line_quantity(line: dict) -> str | None:
    """A completion line's quantity with its unit -- pallets and loose boxes
    otherwise both read as a bare "x1". None when the line carries neither
    count (a picks-only line)."""
    if line.get("pallet_count") is not None:
        return f"x{line['pallet_count']}托"
    if line.get("box_count") is not None:
        return f"散箱x{line['box_count']}"
    return None


def _sku_summary(lines: list[dict], sku_labels: dict) -> str:
    parts = []
    for line in lines:
        label = sku_labels.get(line.get("sku_code"), line.get("sku_code", "?"))
        quantity = _line_quantity(line)
        if quantity is None:
            picked = sum(p.get("box_count") or 0 for p in line.get("picks") or [])
            quantity = f"{picked}箱" if picked else "x?"
        parts.append(f"{label} {quantity}")
    return "; ".join(parts)


def invoice_filename(warehouse_codes, start_month: str, end_month: str | None) -> str:
    """invoice_DE-JFK-NJ_2026-09_2026-09.xlsx -- the warehouses it covers, sorted."""
    codes = invoice_warehouse_list(warehouse_codes)
    return f"invoice_{'-'.join(codes)}_{start_month}_{end_month or start_month}.xlsx"


_CHARGE_ROWS = (
    ("Transportation fee", "transportation_fee"),
    ("Palletization fee", "palletization_fee"),
    ("Container unpacking fee", "unpacking_fee"),
    ("Storage fee", "storage_fee"),
)


def _write_summary_sheet(ws, combined: dict, start_month: str, end_month: str, generated_at: datetime) -> None:
    """
    Charges by warehouse: one column per warehouse plus a Total column, with
    a Total row; totals are SUM formulas. A single-warehouse invoice uses the
    same layout with one warehouse column (decision D1).
    """
    codes = combined["warehouse_codes"]
    per = combined["per_warehouse"]
    last_wh_col = get_column_letter(1 + len(codes))
    total_col = len(codes) + 2

    ws.title = "Summary"
    ws.append(["Warehouses", ", ".join(codes)])
    ws.append(["Range", f"{start_month} to {end_month}"])
    ws.append(["Generated at (UTC)", generated_at.strftime("%Y-%m-%d %H:%M")])
    ws.append([None])  # spacer row (append([]) writes nothing)

    _write_header(ws, ws.max_row + 1, ["Charge (USD)", *codes, "Total"])
    first_charge = ws.max_row + 1
    for label, key in _CHARGE_ROWS:
        row = ws.max_row + 1
        ws.append([label, *(float(per[c][key]) for c in codes), f"=SUM(B{row}:{last_wh_col}{row})"])
    last_charge = ws.max_row
    total_row = ws.max_row + 1
    ws.append(["Total", *(
        f"=SUM({get_column_letter(col)}{first_charge}:{get_column_letter(col)}{last_charge})"
        for col in range(2, total_col + 1)
    )])
    for col in range(1, total_col + 1):
        ws.cell(row=total_row, column=col).font = _HEADER_FONT

    ws.append([None])  # spacer row (append([]) writes nothing)
    _write_header(ws, ws.max_row + 1, ["Inventory (plt)", *codes, "Total"])
    for label, key in (("Opening pallets", "opening_pallets"), ("Closing pallets", "closing_pallets")):
        row = ws.max_row + 1
        ws.append([label, *(per[c][key] for c in codes), f"=SUM(B{row}:{last_wh_col}{row})"])
    _autosize(ws)


def build_invoice_workbook(
    db: DBSession, warehouse_codes, start_month: str, end_month: str | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    """The workbook bytes only; see build_invoice_report."""
    return build_invoice_report(db, warehouse_codes, start_month, end_month, generated_at)[0]


def build_invoice_report(
    db: DBSession, warehouse_codes, start_month: str, end_month: str | None = None,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict]:
    """
    Returns (workbook bytes, the combined invoice it was built from).
    Callers use that same dict for the chat reply, so the reply and the
    attached file can never describe different reads of the data (Codex
    code audit #4): the fees are computed once, and the per-SKU balances are
    read once and feed the Inventory sheet, the Summary pallet lines and
    the reply's opening/closing totals alike.

    warehouse_codes: one code or a list (invoice-inventory plan, Phase 3);
    the workbook covers them all, every detail row naming its warehouse.

    generated_at: pass a stable, persisted timestamp (e.g. RequestLog.created_at)
    for any caller that needs byte-identical regeneration -- Kefu's durable
    delivery queue verifies a content hash before every send (core/kefu_delivery.py),
    so a wall-clock default here would make every retry/redelivery mismatch
    and fail permanently, the same non-idempotent trap
    handlers/uchoice/pdf_stub.py's delivery_date already avoids via
    RequestLog.created_at. Also fixes the workbook's own created/modified
    properties to the same value -- openpyxl stamps those with datetime.now()
    by default on every save, which alone is enough to make two otherwise-
    identical workbooks hash differently.
    Defaults to datetime.now() for Smart Robot's one-shot, never-re-verified
    send path, where determinism doesn't matter.
    """
    codes = invoice_warehouse_list(warehouse_codes)
    end_month = end_month or start_month
    start, _end, end_exclusive = _resolve_range(start_month, end_month)
    # Fee rows and stock balances are each read once; totals, sheets and the
    # returned dict (the chat reply) are all derived from those reads.
    rows_by_warehouse = {code: select_invoice_rows(db, code, start_month, end_month) for code in codes}
    combined = compute_combined_invoice(db, codes, start_month, end_month, include_inventory=False,
                                        rows_by_warehouse=rows_by_warehouse)
    balances = inventory_balances(db, codes, start, end_exclusive)
    for key, field in (("opening_pallets", "opening"), ("closing_pallets", "closing")):
        for code in codes:
            combined["per_warehouse"][code][key] = sum(b[field] for b in balances if b["warehouse_code"] == code)
        combined[key] = sum(b[field] for b in balances)
    sku_labels = sku_label_map(db)
    generated_at = generated_at or datetime.now(timezone.utc)

    wb = Workbook()
    wb.properties.created = generated_at
    wb.properties.modified = generated_at

    _write_summary_sheet(wb.active, combined, start_month, end_month, generated_at)
    _write_inventory_sheet(wb, balances, sku_labels)

    # ── Transportation & Palletization (outbound completions) ──────────────
    from models.uchoice import UchoiceAddress

    # Every detail row carries its own Warehouse, so a row copied out of
    # this workbook stays identifiable. Rows are grouped by warehouse
    # (sorted), then by completion time.
    ws2 = wb.create_sheet("Outbound")
    _write_header(ws2, 1, [
        "Warehouse", "Serial Number", "Completed At (UTC)", "SKU Lines",
        "Destination Company", "Destination Address",
        "Transportation Fee", "Palletization Fee",
    ])
    for warehouse_code in codes:
        for log in rows_by_warehouse[warehouse_code].outbound:
            result = log.result or {}
            original_fields = get_original_fields(db, log)
            lines = result.get("fulfillment_lines") or []
            if any(_line_quantity(l) is None for l in lines):
                # Batch completions recorded before this fix stored picks-only
                # lines; a batch ships at the original quantities, so those
                # are the lines to show.
                lines = original_fields.get("sku_lines") or lines
            sku_summary = _sku_summary(lines, sku_labels)

            # destination isn't in result — it's on the original request, not the
            # completion's own fields, so it's resolved the same way the
            # confirmation/response builders do (core/uchoice_context.py).
            destination_company = ""
            destination_addr = ""
            destination_address_id = original_fields.get("destination_address_id")
            if destination_address_id:
                addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
                if addr:
                    destination_company = addr.company_name or ""
                    destination_addr = addr.addr

            ws2.append([
                warehouse_code,
                log.serial_number,
                log.completed_at.strftime("%Y-%m-%d %H:%M") if log.completed_at else "",
                sku_summary,
                destination_company,
                destination_addr,
                float(Decimal(str(result.get("transportation_fee", 0)))),
                float(Decimal(str(result.get("palletization_fee", 0)))),
            ])
    _finish_detail_sheet(ws2)

    # ── Unpacking (inbound completions) ─────────────────────────────────────
    ws3 = wb.create_sheet("Inbound")
    # Container # sits next to the fee it explains (unpacking-fee plan D8).
    _write_header(ws3, 1, ["Warehouse", "Serial Number", "Completed At (UTC)", "SKU Lines",
                           "Container #", "Container Unpacking Fee"])
    for warehouse_code in codes:
        for log in rows_by_warehouse[warehouse_code].inbound:
            result = log.result or {}
            sku_summary = _sku_summary(result.get("received_lines") or [], sku_labels)
            ws3.append([
                warehouse_code,
                log.serial_number,
                log.completed_at.strftime("%Y-%m-%d %H:%M") if log.completed_at else "",
                sku_summary,
                result.get("container_number") or "",
                float(Decimal(str(result.get("unpacking_fee", 0)))),
            ])
    _finish_detail_sheet(ws3)

    # ── Storage (daily ledger) ───────────────────────────────────────────
    ws4 = wb.create_sheet("Storage")
    _write_header(ws4, 1, ["Warehouse", "Date", "Pallet Count", "Storage Fee"])
    for warehouse_code in codes:
        for row in rows_by_warehouse[warehouse_code].ledger:
            ws4.append([warehouse_code, row.fee_date.isoformat(), row.pallet_count, float(row.storage_fee)])
    _finish_detail_sheet(ws4)

    buf = io.BytesIO()
    wb.save(buf)
    return freeze_xlsx_timestamps(buf.getvalue(), generated_at), combined


def build_invoice_artifact(
    db: DBSession, warehouse_codes, start_month: str, end_month: str | None, request_log_id
) -> dict:
    """The artifact mapping only; see build_invoice_artifact_with_summary."""
    return build_invoice_artifact_with_summary(db, warehouse_codes, start_month, end_month, request_log_id)[0]


def build_invoice_artifact_with_summary(
    db: DBSession, warehouse_codes, start_month: str, end_month: str | None, request_log_id
) -> tuple[dict, dict]:
    """
    Returns (artifact mapping, the combined invoice the file was built from;
    use it for the chat reply -- see build_invoice_report).

    Channel-neutral artifact wrapper around build_invoice_workbook, matching
    the {bytes, filename, content_type, artifact_key} shape handlers/uchoice/
    pdf_stub.py's PDF artifacts use -- so Kefu delivery (core/kefu_delivery.py's
    enqueue_file) and its replay path (core/kefu_artifact_loader.py) can
    handle an invoice workbook exactly like any other durable Kefu file, no
    Excel-specific casing needed there. artifact_key is stable per (request,
    doc_type) for the same idempotent-regeneration reason PDFs use it.

    generated_at is read from the persisted RequestLog.created_at, mirroring
    handlers/uchoice/pdf_stub.py's delivery_date -- both call sites
    (core/kefu_turn_apply.py's initial build, core/kefu_artifact_loader.py's
    later regeneration) pass a request_log_id whose row already exists by
    construction, so this stays stable across retries/regeneration.
    """
    from models.request_log import RequestLog

    end_month = end_month or start_month
    log = db.query(RequestLog).filter_by(log_id=request_log_id).first() if request_log_id else None
    generated_at = log.created_at if log is not None else None
    data, combined = build_invoice_report(db, warehouse_codes, start_month, end_month, generated_at=generated_at)
    return {
        "bytes": data,
        "filename": invoice_filename(warehouse_codes, start_month, end_month),
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "artifact_key": f"{request_log_id}:invoice_workbook",
    }, combined
