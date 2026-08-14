"""
Generic confirmation template renderer. Replaces the old hardcoded
shipper_*/recipient_* prefix-matching logic with a pure renderer + a
registry-with-default-fallback for building the (display_name, sections)
that go into it — mirroring handlers/registry.py's idiom. Most services
(flat scalar fields, nothing to look up) never need a registry entry.
"""
from typing import Callable
from sqlalchemy.orm import Session as DBSession
from core.message_sections import render_sections


def build_confirmation_message(
    serial_number: str,
    service_display_name: str,
    sections: list[dict],
    note: str | None = None
) -> str:
    """
    Pure renderer — no service-specific logic. sections is a list of
    {"label": str, "type": "kv"|"list", "items": dict|list}.
    """
    lines = [
        "**请确认以下信息**",
        f"申请编号：{serial_number}",
        f"服务类型：{service_display_name}",
    ]

    lines += render_sections(sections)

    lines += ["", '回复 **确认** 提交申请，或 **取消** 放弃。']

    if note:
        lines += ["", f"> 注意：{note}"]

    return "\n".join(lines)


# ── Display name resolution ─────────────────────────────────────────────────

_DISPLAY_NAMES = {
    "fedex_label":                "FedEx 快递标签",
    "ups_label":                  "UPS 快递标签",
    "uchoice_inbound_request":    "U-Choice 入库申请",
    "uchoice_outbound_request":   "U-Choice 出库申请",
    "confirm_inbound_completion": "入库完成确认",
    "confirm_outbound_completion":"出库完成确认",
    "adjust_storage":             "库存调整",
    "recount_storage":            "库存盘点",
    "move_storage":               "库存内部调拨",
    "upsert_address":             "地址簿更新",
    "role_change":                "角色变更",
    "view_storage":               "查询库存",
    "view_storage_history":       "库存变动记录",
    "view_invoice":               "费用报告",
    "explain_service":            "服务说明",
    "view_pending_digest":        "待处理事项查询",
    "purge_kefu_sessions":        "清空进行中会话",
}


def _fedex_display_name(service_type_name: str, collected_fields: dict) -> str:
    name = _DISPLAY_NAMES.get(service_type_name, service_type_name)
    if collected_fields.get("oms_outbound_order_no"):
        name += "（关联OMS出库单）"
    return name


_DISPLAY_NAME_BUILDERS: dict[str, Callable[[str, dict], str]] = {
    "fedex_label": _fedex_display_name,
}


def build_display_name(service_type_name: str, collected_fields: dict) -> str:
    builder = _DISPLAY_NAME_BUILDERS.get(service_type_name)
    if builder:
        return builder(service_type_name, collected_fields)
    return _DISPLAY_NAMES.get(service_type_name, service_type_name)


# ── Field labels (Chinese) ──────────────────────────────────────────────────

_FIELD_LABELS = {
    # shipper / recipient (FedEx/UPS)
    "shipper_name": "姓名", "shipper_corp_name": "公司", "shipper_phone": "电话",
    "shipper_street": "地址", "shipper_city": "城市", "shipper_state": "州/省",
    "shipper_zip": "邮编", "shipper_country": "国家",
    "recipient_name": "姓名", "recipient_corp_name": "公司", "recipient_phone": "电话",
    "recipient_street": "地址", "recipient_city": "城市", "recipient_state": "州/省",
    "recipient_zip": "邮编", "recipient_country": "国家",
    "weight_lbs": "重量（磅）", "service_level": "服务等级", "length_in": "长度（英寸）",
    "width_in": "宽度（英寸）", "height_in": "高度（英寸）", "reference_number": "参考编号",
    # U-Choice
    "warehouse_code": "仓库", "sku_lines": "商品明细", "needs_unpacking": "需要拆包",
    "destination_address_id": "目的地地址ID", "new_pallet_count": "新增打托数",
    "reference_serial": "关联申请编号", "received_lines": "实收明细",
    "fulfillment_lines": "实发明细", "adjustment_lines": "调整明细",
    "inventory_lines": "盘点明细", "move_lines": "调拨明细", "company_name": "公司名称",
    "charge_type": "计费类型", "addr": "地址", "note": "备注",
    "target_openid": "目标用户", "new_role": "新角色", "target_month": "月份",
    "sku_code": "商品编码",
}


def _field_label(field_key: str) -> str:
    return _FIELD_LABELS.get(field_key, field_key)


_CHARGE_TYPE_LABELS = {
    "short_delivery":  "短途配送",
    "delivery":        "配送",
    "truck_transfer":  "卡车转仓",
    "self_pickup":     "自提",
}


def charge_type_label(code: str | None) -> str:
    from core.uchoice_rates import CHARGE_TYPE_RATES
    label = _CHARGE_TYPE_LABELS.get(code, code or "?")
    rate = CHARGE_TYPE_RATES.get(code)
    return f"{label}（${rate}）" if rate is not None else label


_ROLE_LABELS = {
    "admin":        "管理员",
    "customer":     "客户",
    "warehouseman": "仓库管理员",
    "accountant":   "财务",
}


def role_label(code: str | None) -> str:
    return _ROLE_LABELS.get(code, code or "?")


def member_display_label(db: DBSession, wechat_openid: str | None) -> str:
    """
    Resolves a wechat_openid to "display_name（wechat_openid）" for confirm/
    response messages, falling back to the bare ID if unresolvable. Looked
    up by wechat_openid alone (GroupMember's real PK also includes group_id,
    not available to confirmation builders) — acceptable given the current
    one-shared-group-per-deployment model, and the raw ID is always shown
    alongside as the authoritative value regardless.
    """
    from models.group import GroupMember
    if not wechat_openid:
        return "?"
    member = db.query(GroupMember).filter_by(wechat_openid=wechat_openid).first()
    if member and member.display_name:
        return f"{member.display_name}（{wechat_openid}）"
    return wechat_openid


# ── SKU label resolution ─────────────────────────────────────────────────────

def _sku_label_map(db: DBSession) -> dict[str, str]:
    from core.uchoice_context import sku_label_map
    return sku_label_map(db)


def _sku_label(sku_labels: dict[str, str], sku_code: str) -> str:
    return sku_labels.get(sku_code, sku_code)


# ── Sections builders ────────────────────────────────────────────────────────

def _label_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """FedEx/UPS — preserves the original shipper/recipient/package grouping."""
    oms_order_no = collected_fields.get("oms_outbound_order_no")
    shipper   = {_field_label(k): v for k, v in collected_fields.items() if k.startswith("shipper_")}
    recipient = {_field_label(k): v for k, v in collected_fields.items() if k.startswith("recipient_")}
    other     = {
        _field_label(k): v for k, v in collected_fields.items()
        if not k.startswith("shipper_") and not k.startswith("recipient_") and k != "oms_outbound_order_no"
    }

    sections = []
    if oms_order_no:
        sections.append({"label": "订单信息", "type": "kv", "items": {"OMS出库单号": oms_order_no}})
    sections.append({"label": "发件人", "type": "kv", "items": shipper})
    sections.append({"label": "收件人", "type": "kv", "items": recipient})
    if other:
        sections.append({"label": "包裹信息", "type": "kv", "items": other})
    return sections


def _inbound_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    uchoice_inbound_request — resolve sku_code to a human-readable product
    name, highlight quantity with WeChat markdown's supported color tag, and
    only show what actually matters at confirm time: warehouse + qty per
    line. needs_unpacking is only shown when true — no boilerplate "否" line.

    Sorted by (sku_code, boxes_per_pallet) rather than the order the AI
    happened to extract them in — also avoids a dict-collision bug: keying
    by product label alone would silently drop a second line for the same
    SKU at a different boxes_per_pallet (a legitimate case per the design's
    free-boxes_per_pallet model), so this uses a plain sorted list instead.
    """
    sku_labels = _sku_label_map(db)
    raw_lines = collected_fields.get("sku_lines", []) or []
    sorted_lines = sorted(raw_lines, key=lambda l: (l.get("sku_code", ""), l.get("boxes_per_pallet", l.get("box_count", 0))))

    formatted = []
    for line in sorted_lines:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        if "box_count" in line:
            qty = f'散箱 x{line["box_count"]}'
        else:
            pallet_count = line.get("pallet_count", "?")
            bpp = line.get("boxes_per_pallet", "?")
            qty = f'{pallet_count} 托 @ {bpp}/托'
        formatted.append(f"{label}：{qty}")

    warehouse_code = collected_fields.get("warehouse_code", "?")
    # Inbound and outbound both default an unstated warehouse_code to JFK and
    # expose the same auto-default note.
    warehouse_note = "，系统默认，如有误请更正" if collected_fields.get("_warehouse_auto_default") else ""
    sections = [{"label": f"入库明细（{warehouse_code} 仓{warehouse_note}）", "type": "list", "items": formatted}]

    if collected_fields.get("needs_unpacking"):
        sections.append({
            "label": None,
            "type": "list",
            "items": ['需要拆包（+$300）'],
        })
    return sections


def _outbound_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    uchoice_outbound_request — same treatment as uchoice_inbound_request:
    sorted list (not dict, avoids the same same-SKU-different-bucket
    collision), colored qty, warehouse in the section header. Still must
    surface the AI's largest-bucket default explicitly (safety net for the
    no-guess rule) — that note is a flagged AI assumption, not routine
    boilerplate, so it stays even though other optional fields got trimmed.
    destination_address_id is resolved to the real company/address, not
    left as a raw UUID. new_pallet_count only shown when > 0.
    """
    from models.uchoice import UchoiceAddress
    from core.uchoice_rates import PALLETIZATION_PER_PALLET

    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code", "?")
    warehouse_note = "，系统默认，如有误请更正" if collected_fields.get("_warehouse_auto_default") else ""
    raw_lines = collected_fields.get("sku_lines", []) or []
    sorted_lines = sorted(raw_lines, key=lambda l: (l.get("sku_code", ""), l.get("boxes_per_pallet", l.get("box_count", 0))))

    formatted = []
    for line in sorted_lines:
        sku = line.get("sku_code", "?")
        label = _sku_label(sku_labels, sku)
        if "box_count" in line:
            formatted.append(f'{label}：散箱 x{line["box_count"]}')
            continue

        # boxes_per_pallet is resolved and persisted to collected_fields by
        # workflow_engine._resolve_outbound_pallet_defaults before this
        # confirmation is ever built — this only renders the flag it left,
        # rather than re-deriving the default itself (see that function's
        # docstring for why a display-only default was the original bug).
        bpp = line.get("boxes_per_pallet", "未知")
        default_note = '　（系统自动选择，如有误请更正）' if line.get("_bpp_auto_default") else ""
        pallet_count = line.get("pallet_count", "?")
        formatted.append(f'{label}：{pallet_count} 托 @ {bpp}/托{default_note}')

    sections = [{"label": f"出库明细（{warehouse_code} 仓{warehouse_note}）", "type": "list", "items": formatted}]

    dest_id = collected_fields.get("destination_address_id")
    if dest_id:
        from core.uchoice_context import format_address_label
        addr = db.query(UchoiceAddress).filter_by(address_id=dest_id).first()
        dest_label = format_address_label(addr)
        sections.append({"label": None, "type": "list", "items": [f"目的地：{dest_label}"]})

    new_pallet_count = collected_fields.get("new_pallet_count")
    if new_pallet_count:
        fee = new_pallet_count * PALLETIZATION_PER_PALLET
        sections.append({
            "label": None, "type": "list",
            "items": [f'打板数量：{new_pallet_count} 托（+${fee}）'],
        })

    return sections


def _inbound_completion_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    confirm_inbound_completion — must show the EFFECTIVE lines about to be
    applied, whether the warehouseman restated them or they're defaulting
    from the original request (reference_serial is now required, so it's
    guaranteed present here — see V5 migration). Without this, a
    warehouseman relying on the default could confirm completely blind to
    what's about to be applied.
    """
    from core.uchoice_context import resolve_completion_target

    reference_serial = collected_fields.get("reference_serial")
    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        msg = f"未找到申请编号 {reference_serial}" if reference_serial else "未能确定关联申请"
        return [{"label": None, "type": "list", "items": [f"⚠️ {msg}"]}]

    sku_labels = _sku_label_map(db)
    restated = collected_fields.get("received_lines")
    effective_lines = restated or original_fields.get("sku_lines", [])
    sorted_lines = sorted(
        effective_lines,
        key=lambda l: (l.get("sku_code", ""), l.get("boxes_per_pallet", l.get("box_count", 0))),
    )

    formatted = []
    for line in sorted_lines:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        if "box_count" in line:
            formatted.append(f'{label}：散箱 x{line["box_count"]}')
        else:
            pallet_count = line.get("pallet_count", "?")
            bpp = line.get("boxes_per_pallet", "?")
            formatted.append(f'{label}：{pallet_count} 托 @ {bpp}/托')

    warehouse_code = original_fields.get("warehouse_code", "?")
    sections = [{"label": f"关联申请 {reference_serial}（{warehouse_code} 仓）", "type": "list", "items": formatted}]
    if not restated:
        sections.append({"label": None, "type": "list", "items": ["（沿用原申请数量，如实收数量有出入请重新说明）"]})
    return sections


def _outbound_completion_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """confirm_outbound_completion — same treatment, plus loose-box picks."""
    from core.uchoice_context import resolve_completion_target
    from models.uchoice import UchoiceStorage

    reference_serial = collected_fields.get("reference_serial")
    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        msg = f"未找到申请编号 {reference_serial}" if reference_serial else "未能确定关联申请"
        return [{"label": None, "type": "list", "items": [f"⚠️ {msg}"]}]

    sku_labels = _sku_label_map(db)
    restated = collected_fields.get("fulfillment_lines")
    effective_lines = restated or original_fields.get("sku_lines", [])
    sorted_lines = sorted(
        effective_lines,
        key=lambda l: (l.get("sku_code", ""), l.get("boxes_per_pallet", l.get("box_count", 0))),
    )
    warehouse_code = original_fields.get("warehouse_code", "?")

    formatted = []
    for line in sorted_lines:
        sku = line.get("sku_code", "?")
        label = _sku_label(sku_labels, sku)
        if "picks" in line:
            pick_strs = []
            for p in line["picks"]:
                note = "（系统自动选择）" if p.get("_auto_default") else ""
                pick_strs.append(f'{p["box_count"]}箱@{p["source_boxes_per_pallet"]}/托{note}')
            formatted.append(f'{label}：散箱发货 ' + "，".join(pick_strs))

            buckets = (
                db.query(UchoiceStorage)
                .filter_by(warehouse_code=warehouse_code, sku_code=sku)
                .filter(UchoiceStorage.pallet_count > 0)
                .order_by(UchoiceStorage.boxes_per_pallet.asc())
                .all()
            )
            if buckets:
                stock_str = "，".join(f'{b.boxes_per_pallet}/托 x{b.pallet_count}' for b in buckets)
                formatted.append(f'　当前库存：{stock_str}')
        elif "box_count" in line:
            formatted.append(f'{label}：散箱 x{line["box_count"]}')
        else:
            pallet_count = line.get("pallet_count", "?")
            bpp = line.get("boxes_per_pallet", "?")
            formatted.append(f'{label}：{pallet_count} 托 @ {bpp}/托')

    sections = [{"label": f"关联申请 {reference_serial}（{warehouse_code} 仓）", "type": "list", "items": formatted}]
    if not restated:
        sections.append({"label": None, "type": "list", "items": ["（按原申请数量发货，如实发数量有出入请重新说明）"]})

    destination_address_id = original_fields.get("destination_address_id")
    if destination_address_id:
        from models.uchoice import UchoiceAddress
        addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
        if addr and addr.destination_warehouse_code:
            sections.append({
                "label": None, "type": "list",
                "items": [f"⚠️ 此为内部调仓：确认后将同时增加 {addr.destination_warehouse_code} 仓对应库存"],
            })

    return sections


def _adjust_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    adjust_storage — same treatment as the other line-item services: sorted
    list (not dict — avoids collision if two lines touch the same
    sku+boxes_per_pallet with different reasons), colored delta, warehouse
    in the section header.
    """
    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code", "?")
    raw_lines = collected_fields.get("adjustment_lines", []) or []
    sorted_lines = sorted(raw_lines, key=lambda l: (l.get("sku_code", ""), l.get("boxes_per_pallet", 0)))

    formatted = []
    for line in sorted_lines:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        bpp = line.get("boxes_per_pallet", "?")
        delta = line.get("pallet_delta", 0)
        reason = line.get("reason", "")
        delta_str = f'{delta:+d}' if isinstance(delta, int) else f'{delta}'
        line_str = f"{label} @ {bpp}/托：{delta_str} 托"
        if reason:
            line_str += f"（{reason}）"
        formatted.append(line_str)

    return [{"label": f"库存调整明细（{warehouse_code} 仓）", "type": "list", "items": formatted}]


def _move_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """move_storage — same treatment: sorted list, colored count, warehouse in the header."""
    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code", "?")
    raw_lines = collected_fields.get("move_lines", []) or []
    sorted_lines = sorted(raw_lines, key=lambda l: (l.get("sku_code", ""), l.get("source_boxes_per_pallet", 0)))

    formatted = []
    for line in sorted_lines:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        src = line.get("source_boxes_per_pallet", "?")
        tgt = line.get("target_boxes_per_pallet", "?")
        count = line.get("box_count_moved", "?")
        formatted.append(f'{label}：从 {src}/托 移动 {count} 箱到 {tgt}/托')

    return [{"label": f"库存调拨明细（{warehouse_code} 仓）", "type": "list", "items": formatted}]


def _recount_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    recount_storage — must show the COMPUTED DIFF against current balances,
    not the raw snapshot the warehouseman typed in.
    """
    from models.uchoice import UchoiceStorage

    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code", "?")
    reported = {
        (l["sku_code"], l["boxes_per_pallet"]): l["pallet_count"]
        for l in collected_fields.get("inventory_lines", []) or []
    }
    current_rows = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
    current = {(r.sku_code, r.boxes_per_pallet): r.pallet_count for r in current_rows}

    formatted = []
    for key in sorted(set(reported) | set(current)):
        sku, bpp = key
        before = current.get(key, 0)
        after = reported.get(key, 0)
        delta = after - before
        if delta == 0:
            continue
        label = _sku_label(sku_labels, sku)
        formatted.append(f'{label} @ {bpp}/托：{before} → {after}（{delta:+d}）')

    if not formatted:
        formatted = ["（无变化）"]

    return [{"label": f"{warehouse_code} 仓库存盘点差异", "type": "list", "items": formatted}]


def _address_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """upsert_address — must state create-vs-update mode explicitly. charge_type shown as its Chinese label, not the raw enum code."""
    mode = "更新" if collected_fields.get("matched_address_id") else "新增"
    items = {}
    for k, v in collected_fields.items():
        if k == "matched_address_id":
            continue
        items[_field_label(k)] = charge_type_label(v) if k == "charge_type" else v
    return [{"label": f"您正在{mode}此地址", "type": "kv", "items": items}]


def _role_change_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """role_change — resolve target_openid to a display name and new_role to its Chinese label."""
    target_openid = collected_fields.get("target_openid")
    new_role = collected_fields.get("new_role")
    warehouse_code = collected_fields.get("warehouse_code")

    items = {
        "目标成员": member_display_label(db, target_openid),
        "新角色":   role_label(new_role),
    }
    if warehouse_code:
        items["负责仓库"] = warehouse_code
    return [{"label": None, "type": "kv", "items": items}]


def _default_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    items = {_field_label(k): v for k, v in collected_fields.items()}
    return [{"label": "详情", "type": "kv", "items": items}]


CONFIRMATION_BUILDERS: dict[str, Callable[[dict, DBSession], list[dict]]] = {
    "fedex_label":              _label_sections_builder,
    "ups_label":                _label_sections_builder,
    "uchoice_inbound_request":    _inbound_sections_builder,
    "uchoice_outbound_request":   _outbound_sections_builder,
    "confirm_inbound_completion": _inbound_completion_sections_builder,
    "confirm_outbound_completion": _outbound_completion_sections_builder,
    "adjust_storage":           _adjust_sections_builder,
    "recount_storage":          _recount_sections_builder,
    "move_storage":             _move_sections_builder,
    "upsert_address":           _address_sections_builder,
    "role_change":              _role_change_sections_builder,
}


def build_confirmation_sections(service_type_name: str, collected_fields: dict, db: DBSession) -> list[dict]:
    builder = CONFIRMATION_BUILDERS.get(service_type_name, _default_sections_builder)
    return builder(collected_fields, db)
