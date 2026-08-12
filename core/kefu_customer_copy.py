"""Customer-safe, copy-ready Kefu response rendering.

This module deliberately builds messages from an allowlist of fields.  It
never filters an internal message after the fact, so adding a new internal
field cannot accidentally expose it to a customer.
"""
from __future__ import annotations


_SERVICE_LABELS = {
    "uchoice_inbound_request": "入库申请",
    "uchoice_outbound_request": "出库申请",
    "upsert_address": "地址信息更新",
}


def _line_text(db, line: dict) -> str:
    from core.uchoice_context import sku_label_map

    labels = sku_label_map(db)
    sku = line.get("sku_code")
    label = labels.get(sku, sku or "商品")
    if "box_count" in line:
        count = line.get("box_count")
        return f"{label}：{count} 箱" if count is not None else f"{label}：数量待确认"
    pallet_count, bpp = line.get("pallet_count"), line.get("boxes_per_pallet")
    if pallet_count is None or bpp is None:
        # pre_confirm_validators.py's per-service quantity checks are the
        # real backstop against this ever being missing at confirm time --
        # this is a last-resort fallback only, never a raw Python None
        # leaking into customer-facing text (observed live on
        # REQ-20260812-001179 before that validator existed).
        return f"{label}：数量待确认"
    return f"{label}：{pallet_count} 托（{bpp} 箱/托）"


def render_customer_copy(db, *, service_name: str | None, session, request_log, context: dict) -> str | None:
    """
    Return a customer-safe success message, or ``None`` when not applicable.

    Deliberately independent of ``session.customer_id``: every current
    U-Choice service is performed on behalf of U-Choice itself (the sole
    platform tenant today), never on behalf of one of the destination
    companies in the address book -- `customer_id` identifies a future
    second *tenant*, not "which company this copy is safe to paste to". Gating
    on it here was a category error carried over from the same mistaken
    premise as the old address-partitioning rule (see docs/ai-collaboration
    /decisions.md's "Superseded or challenged assumptions").
    """
    if service_name not in _SERVICE_LABELS or session is None or session.status != "completed":
        return None

    serial = request_log.serial_number if request_log is not None else ""
    lines = [
        f"您的{_SERVICE_LABELS[service_name]}已提交。",
        f"申请编号：{serial}",
    ]
    fields = session.collected_fields or {}

    if service_name in {"uchoice_inbound_request", "uchoice_outbound_request"}:
        warehouse = fields.get("warehouse_code")
        if warehouse:
            lines.append(f"仓库：{warehouse}")
        sku_lines = fields.get("sku_lines") or []
        if sku_lines:
            lines.append("商品明细：")
            lines.extend(f"- {_line_text(db, line)}" for line in sku_lines if isinstance(line, dict))

    if service_name == "uchoice_outbound_request" and fields.get("destination_address_id"):
        from models.uchoice import UchoiceAddress

        address = db.query(UchoiceAddress).filter_by(address_id=fields["destination_address_id"]).first()
        if address is not None:
            # Company/address are customer-owned logistics data.  Internal
            # charge type, notes, UUIDs, staff identity and stock are omitted.
            lines.append(f"送货地址：{address.company_name}，{address.addr}")

    if service_name == "upsert_address":
        company = fields.get("company_name")
        address = fields.get("addr")
        if company:
            lines.append(f"公司：{company}")
        if address:
            lines.append(f"地址：{address}")

    lines.append("我们会按以上信息处理，如需修改请及时联系我们。")
    return "\n".join(lines)


def compose_staff_reply(internal_text: str, customer_copy_text: str | None) -> str:
    if not customer_copy_text:
        return internal_text
    return f"{internal_text}\n\n【可复制给客户】\n{customer_copy_text}"
