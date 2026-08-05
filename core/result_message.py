"""
Post-execution "it worked" message renderer — the counterpart to
core/confirmation.py's pre-execution summary. Same registry-with-default-
fallback idiom: RESULT_BUILDERS keyed by service_type_name, each building
`sections` ({"label", "type": "kv"|"list", "items"}) from the full pipeline
context (result + collected_fields + any handler-set scratch data like
context["_uchoice_target"]) and db (for label lookups like sku_code ->
description). Dispatch is by the service's real name — handlers/reply_wechat.py
resolves it once via context["service_type_id"], not by sniffing which keys
happen to be present in `result`.
"""
from typing import Callable
from sqlalchemy.orm import Session as DBSession
import config
from core.message_sections import render_sections
from core.confirmation import build_display_name

_LABEL_BASE_URL = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")


def build_result_message(title: str, serial_number: str, sections: list[dict]) -> str:
    """Pure renderer — no service-specific logic."""
    lines = [f"✅ {title}", f"申请编号：{serial_number}"]
    lines += render_sections(sections)
    lines.append("如有问题请联系管理员。")
    return "\n".join(lines)


# ── Title resolution ─────────────────────────────────────────────────────────
# Mirrors confirmation.py's build_display_name/_DISPLAY_NAME_BUILDERS split —
# most services get "<display name>已完成"; a couple (FedEx/UPS) keep their
# original bespoke greeting wording exactly.

def _label_result_title(service_type_name: str, context: dict) -> str:
    return f"{context.get('display_name', '')}，您的申请已成功处理！"


def _awaits_completion_result_title(service_type_name: str, context: dict) -> str:
    """
    uchoice_inbound_request / uchoice_outbound_request — these don't actually
    complete here (awaits_completion=true, stays at 'processing' until a
    warehouseman confirms). "已完成" would be misleading; the details were
    already shown in full at the confirm step, so this stays short.
    """
    return "已提交，等待仓库确认操作"


def _inbound_completion_result_title(service_type_name: str, context: dict) -> str:
    return "入库已确认，仓库最新库存如下"


def _outbound_completion_result_title(service_type_name: str, context: dict) -> str:
    return "出库已确认，仓库最新库存如下"


def _address_result_title(service_type_name: str, context: dict) -> str:
    mode = context.get("result", {}).get("mode", "更新")
    return f"地址已{mode}"


_RESULT_TITLE_BUILDERS: dict[str, Callable[[str, dict], str]] = {
    "fedex_label":                 _label_result_title,
    "ups_label":                   _label_result_title,
    "uchoice_inbound_request":     _awaits_completion_result_title,
    "uchoice_outbound_request":    _awaits_completion_result_title,
    "confirm_inbound_completion":  _inbound_completion_result_title,
    "confirm_outbound_completion": _outbound_completion_result_title,
    "upsert_address":              _address_result_title,
}


def build_result_title(service_type_name: str | None, context: dict) -> str:
    if not service_type_name:
        return "操作已完成"
    builder = _RESULT_TITLE_BUILDERS.get(service_type_name)
    if builder:
        return builder(service_type_name, context)
    display = build_display_name(service_type_name, context.get("collected_fields", {}))
    return f"{display}已完成"


# ── Sections builders ────────────────────────────────────────────────────────

def _label_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """fedex_label / ups_label — preserves the original flat layout exactly (no bullets/spacers)."""
    result = context.get("result", {})
    tracking_number = result.get("tracking_number", "")
    has_label = bool(result.get("label_base64", ""))

    items = [f"标签追踪号：{tracking_number}"]
    if has_label:
        serial_number = context.get("serial_number", "")
        label_url = f"{_LABEL_BASE_URL}/labels/{serial_number}"
        items.append(f"[点击下载标签]({label_url})")
    return [{"type": "raw", "items": items}]


_TXN_TYPE_LABELS = {
    "inbound": "入库", "outbound": "出库",
    "convert_in": "转换入", "convert_out": "转换出",
    "move_in": "调拨入", "move_out": "调拨出",
    "transfer_in": "转仓入", "transfer_out": "转仓出",
    "adjust": "调整", "recount": "盘点",
}


def _format_sku_bucket_lines(rows: list[dict], sku_labels: dict[str, str]) -> list[str]:
    """
    Shared by view_storage and the completion-response full-storage builder.
    rows are already scoped to one warehouse: {sku_code, boxes_per_pallet,
    pallet_count}. Groups by sku_code (sorted), each line shows every bucket
    for that SKU plus a total when it has more than one bucket — a single-
    bucket SKU shows just the one line, no redundant "(total X)".
    """
    from collections import defaultdict

    by_sku: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sku[r["sku_code"]].append(r)

    lines = []
    for sku_code in sorted(by_sku):
        buckets = sorted(by_sku[sku_code], key=lambda b: b["boxes_per_pallet"])
        label = sku_labels.get(sku_code, sku_code)
        if len(buckets) == 1:
            b = buckets[0]
            lines.append(f'{label}：{b["pallet_count"]} 托 @ {b["boxes_per_pallet"]}/托')
        else:
            bucket_str = "，".join(f'{b["pallet_count"]} 托 @ {b["boxes_per_pallet"]}/托' for b in buckets)
            total = sum(b["pallet_count"] for b in buckets)
            lines.append(f'{label}：{bucket_str}（合计 {total} 托）')
    return lines


def _storage_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """view_storage — split into one section per warehouse, sorted by SKU within each, with per-SKU totals."""
    from collections import defaultdict
    from core.uchoice_context import sku_label_map

    result = context.get("result", {})
    rows = result.get("storage_rows") or []
    if not rows:
        return [{"label": None, "type": "list", "items": ["（无匹配记录）"]}]

    sku_labels = sku_label_map(db)
    by_warehouse: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_warehouse[r["warehouse_code"]].append(r)

    sections = []
    for warehouse_code in sorted(by_warehouse):
        warehouse_rows = by_warehouse[warehouse_code]
        lines = _format_sku_bucket_lines(warehouse_rows, sku_labels)
        warehouse_total = sum(r["pallet_count"] for r in warehouse_rows)
        sections.append({"label": f"{warehouse_code} 仓（共 {warehouse_total} 托）", "type": "list", "items": lines})
    return sections


def _storage_history_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """
    view_storage_history. Header line states the actual queried range
    (capped at today for the current, still-in-progress month — see
    QueryStorageHistoryHandler). Chronological detail list stays
    chronological (that's the point of a history view — not re-grouped by
    SKU), but sorted defensively by created_at here too rather than trusting
    the handler's ORDER BY alone. Ends with a net-change-by-SKU summary plus
    a grand total.
    """
    from datetime import datetime
    from core.uchoice_context import sku_label_map

    result = context.get("result", {})
    rows = result.get("history_rows") or []
    range_start = result.get("range_start")
    range_end = result.get("range_end")
    range_line = f"查询范围：{range_start} 至 {range_end}" if range_start and range_end else None

    if not rows:
        sections = []
        if range_line:
            sections.append({"label": None, "type": "raw", "items": [range_line]})
        sections.append({"label": None, "type": "list", "items": ["（无记录）"]})
        return sections

    sku_labels = sku_label_map(db)
    sorted_rows = sorted(rows, key=lambda r: r.get("created_at") or "")

    detail_lines = []
    net_by_sku: dict[str, int] = {}
    for r in sorted_rows:
        created = r.get("created_at") or ""
        try:
            created = datetime.fromisoformat(created).strftime("%m-%d %H:%M") if created else ""
        except ValueError:
            pass
        txn_label = _TXN_TYPE_LABELS.get(r["txn_type"], r["txn_type"])
        sku_label = sku_labels.get(r["sku_code"], r["sku_code"])
        detail_lines.append(f"{created} {txn_label} {sku_label}@{r['boxes_per_pallet']} {r['pallet_delta']:+d}")
        net_by_sku[r["sku_code"]] = net_by_sku.get(r["sku_code"], 0) + r["pallet_delta"]

    sections = []
    if range_line:
        sections.append({"label": None, "type": "raw", "items": [range_line]})
    sections.append({"label": None, "type": "list", "items": detail_lines})

    summary_lines = [
        f"{sku_labels.get(sku, sku)}：{net:+d} 托"
        for sku, net in sorted(net_by_sku.items())
    ]
    total_net = sum(net_by_sku.values())
    summary_lines.append(f"合计：{total_net:+d} 托")
    sections.append({"label": "净变动", "type": "list", "items": summary_lines})

    return sections


def _invoice_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """view_invoice. Header line states the queried warehouse + range."""
    result = context.get("result", {})
    warehouse_code = result.get("warehouse_code", "?")
    start_month = result.get("start_month", "?")
    end_month = result.get("end_month", start_month)
    range_str = start_month if start_month == end_month else f"{start_month} 至 {end_month}"

    items = {
        "运输费": f"${result.get('transportation_fee', 0)}",
        "打托费": f"${result.get('palletization_fee', 0)}",
        "拆包费": f"${result.get('unpacking_fee', 0)}",
        "仓储费": f"${result.get('storage_fee', 0)}",
        "合计":   f"${result.get('total', 0)}",
    }
    sections = [
        {"label": None, "type": "raw", "items": [f"仓库：{warehouse_code}　范围：{range_str}"]},
        {"label": None, "type": "kv", "items": items},
    ]

    download_url = result.get("download_url")
    if download_url:
        sections.append({"label": None, "type": "raw", "items": [f"[下载详细报表]({download_url})（1小时内有效）"]})

    return sections


def _empty_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """
    uchoice_inbound_request — the confirm step already showed the full line-
    item breakdown; this response is deliberately just the status title +
    footer, nothing restated. Explicit (not relying on the default fallback
    happening to produce nothing) so it stays correct even if
    record_uchoice_request's handler ever starts returning data.
    """
    return []


def _warehouse_storage_summary_sections(db: DBSession, warehouse_code: str | None, label_prefix: str) -> list[dict]:
    """
    Shared "show the full updated storage for one warehouse" block — used by
    every storage-mutating warehouseman action's response (completions,
    adjust, and likely recount/move too) so they can visually verify the
    mutation took effect, without duplicating the query+format logic per
    service. Deliberately warehouse-only, not sku-only — a warehouseman
    wants the whole picture, not just the line they just touched.
    """
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    if not warehouse_code:
        return []

    orm_rows = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
    rows = [{"sku_code": r.sku_code, "boxes_per_pallet": r.boxes_per_pallet, "pallet_count": r.pallet_count} for r in orm_rows]
    sku_labels = sku_label_map(db)
    if not rows:
        return [{"label": f"{warehouse_code} {label_prefix}", "type": "list", "items": ["（无库存记录）"]}]
    lines = _format_sku_bucket_lines(rows, sku_labels)
    warehouse_total = sum(r["pallet_count"] for r in rows)
    return [{"label": f"{warehouse_code} {label_prefix}（共 {warehouse_total} 托）", "type": "list", "items": lines}]


def _completion_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """
    confirm_inbound_completion / confirm_outbound_completion — the
    warehouseman's own final reply shows the AFFECTED WAREHOUSE'S full
    updated storage. context["_uchoice_target"] (set by
    LookupAndValidateCompletionHandler) is still present at reply_wechat
    time since context is one mutable dict threaded through every step.

    This is NOT what gets pushed to the customer's group (that's
    CompleteExistingRequestHandler's short status notification, separately)
    — U-Choice's storage is shared company-wide inventory, not per-customer,
    so dumping the full warehouse table into a customer's group would
    incidentally reveal aggregate business volume unrelated to that customer.
    """
    target = context.get("_uchoice_target", {})
    sections = _warehouse_storage_summary_sections(db, target.get("warehouse_code"), "仓当前库存")

    # Inter-warehouse transfer — also show the destination warehouse's
    # updated storage, since this one confirmation just changed both sides.
    destination_warehouse_code = context.get("result", {}).get("destination_warehouse_code")
    if destination_warehouse_code:
        sections += _warehouse_storage_summary_sections(db, destination_warehouse_code, "仓当前库存")

    return sections


def _adjust_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """adjust_storage — same "show updated storage" treatment as the completion services."""
    warehouse_code = context.get("collected_fields", {}).get("warehouse_code")
    return _warehouse_storage_summary_sections(db, warehouse_code, "仓当前库存")


def _recount_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """recount_storage — same "show updated storage" treatment."""
    warehouse_code = context.get("collected_fields", {}).get("warehouse_code")
    return _warehouse_storage_summary_sections(db, warehouse_code, "仓当前库存")


def _move_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """move_storage — same "show updated storage" treatment."""
    warehouse_code = context.get("collected_fields", {}).get("warehouse_code")
    return _warehouse_storage_summary_sections(db, warehouse_code, "仓当前库存")


def _address_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """
    upsert_address — no storage involved, shows the actual address details
    (from collected_fields, since context["result"] only carries the
    internal address_id + mode) rather than a raw UUID.
    """
    from core.confirmation import charge_type_label

    fields = context.get("collected_fields", {})
    items = {
        "公司": fields.get("company_name", "?"),
        "地址": fields.get("addr", "?"),
        "计费类型": charge_type_label(fields.get("charge_type")),
    }
    if fields.get("warehouse_code"):
        items["所属仓库"] = fields["warehouse_code"]
    if fields.get("note"):
        items["备注"] = fields["note"]
    return [{"label": None, "type": "kv", "items": items}]


def _role_change_result_sections_builder(context: dict, db: DBSession) -> list[dict]:
    """role_change — same display-name + Chinese-role-label resolution as the confirmation."""
    from core.confirmation import member_display_label, role_label

    result = context.get("result", {})
    fields = context.get("collected_fields", {})
    items = {
        "目标成员": member_display_label(db, result.get("target_openid") or fields.get("target_openid")),
        "新角色":   role_label(result.get("new_role") or fields.get("new_role")),
    }
    if fields.get("warehouse_code"):
        items["负责仓库"] = fields["warehouse_code"]
    return [{"label": None, "type": "kv", "items": items}]


RESULT_BUILDERS: dict[str, Callable[[dict, DBSession], list[dict]]] = {
    "fedex_label":                 _label_sections_builder,
    "ups_label":                   _label_sections_builder,
    "view_storage":                _storage_sections_builder,
    "view_storage_history":        _storage_history_sections_builder,
    "view_invoice":                _invoice_sections_builder,
    "uchoice_inbound_request":     _empty_sections_builder,
    "uchoice_outbound_request":    _empty_sections_builder,
    "confirm_inbound_completion":  _completion_result_sections_builder,
    "confirm_outbound_completion": _completion_result_sections_builder,
    "adjust_storage":              _adjust_result_sections_builder,
    "recount_storage":             _recount_result_sections_builder,
    "move_storage":                _move_result_sections_builder,
    "upsert_address":              _address_result_sections_builder,
    "role_change":                 _role_change_result_sections_builder,
}


def _default_sections_builder(context: dict, db: DBSession) -> list[dict]:
    result = context.get("result", {})
    skip_keys = {"pdf_url", "pdf_status", "warehouse_code"}
    items = {k: v for k, v in result.items() if k not in skip_keys and v not in (None, [], {})}
    if not items:
        return []
    return [{"label": None, "type": "kv", "items": items}]


def build_sections(service_type_name: str | None, context: dict, db: DBSession) -> list[dict]:
    builder = RESULT_BUILDERS.get(service_type_name, _default_sections_builder) if service_type_name else _default_sections_builder
    return builder(context, db)
