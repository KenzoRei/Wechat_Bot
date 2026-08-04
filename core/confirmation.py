"""
Generic confirmation template renderer. Replaces the old hardcoded
shipper_*/recipient_* prefix-matching logic with a pure renderer + a
registry-with-default-fallback for building the (display_name, sections)
that go into it — mirroring handlers/registry.py's idiom. Most services
(flat scalar fields, nothing to look up) never need a registry entry.
"""
from typing import Callable
from sqlalchemy.orm import Session as DBSession


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

    for section in sections:
        lines += ["", f"**{section['label']}**"]
        if section["type"] == "kv":
            for key, val in section["items"].items():
                lines.append(f"- {key}：{val}")
        else:  # "list"
            for item in section["items"]:
                lines.append(f"- {item}")

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


# ── SKU label resolution ─────────────────────────────────────────────────────

def _sku_label_map(db: DBSession) -> dict[str, str]:
    """sku_code -> human-readable description, e.g. 't4' -> 'T4 2-inch Clear Packing Tape'."""
    from models.uchoice import UchoiceSku
    return {s.sku_code: s.description for s in db.query(UchoiceSku).all()}


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
    """uchoice_inbound_request — resolve sku_code to a human-readable product name."""
    sku_labels = _sku_label_map(db)
    items = {}
    for line in collected_fields.get("sku_lines", []) or []:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        if "box_count" in line:
            items[label] = f"散箱 x{line['box_count']}"
        else:
            items[label] = f"{line.get('pallet_count', '?')} 托 @ {line.get('boxes_per_pallet', '?')}/托"

    sections = [{"label": "入库明细", "type": "kv", "items": items}]
    if "needs_unpacking" in collected_fields:
        sections.append({
            "label": "拆包费用",
            "type": "kv",
            "items": {"是否需要拆包": "是" if collected_fields.get("needs_unpacking") else "否"},
        })
    return sections


def _outbound_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    uchoice_outbound_request — must surface the AI's largest-bucket default
    explicitly (safety net for the no-guess rule), not silently apply it.
    Also resolves sku_code to a human-readable product name.
    """
    from models.uchoice import UchoiceStorage

    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code")
    items = {}
    for line in collected_fields.get("sku_lines", []) or []:
        sku = line.get("sku_code", "?")
        label = _sku_label(sku_labels, sku)
        if "box_count" in line:
            items[label] = f"散箱 x{line['box_count']}"
            continue

        bpp = line.get("boxes_per_pallet")
        default_note = ""
        if bpp is None:
            bucket = (
                db.query(UchoiceStorage)
                .filter_by(warehouse_code=warehouse_code, sku_code=sku)
                .order_by(UchoiceStorage.pallet_count.desc())
                .first()
            )
            bpp = bucket.boxes_per_pallet if bucket else "未知"
            default_note = "（系统自动选择的默认托盘规格，如有误请更正）"
        items[label] = f"{line.get('pallet_count', '?')} 托 @ {bpp}/托{default_note}"

    sections = [{"label": "出库明细", "type": "kv", "items": items}]
    if collected_fields.get("destination_address_id"):
        sections.append({"label": "目的地", "type": "kv", "items": {"地址ID": collected_fields["destination_address_id"]}})
    sections.append({"label": "打托费用", "type": "kv", "items": {"新增打托数": collected_fields.get("new_pallet_count", 0)}})
    return sections


def _adjust_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """adjust_storage — resolve sku_code to a human-readable product name."""
    sku_labels = _sku_label_map(db)
    items = {}
    for line in collected_fields.get("adjustment_lines", []) or []:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        bpp = line.get("boxes_per_pallet", "?")
        delta = line.get("pallet_delta", 0)
        sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
        reason = line.get("reason", "")
        items[f"{label} @ {bpp}/托"] = f"{sign}{delta} 托（{reason}）" if reason else f"{sign}{delta} 托"
    return [{"label": "库存调整明细", "type": "kv", "items": items}]


def _move_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """move_storage — resolve sku_code to a human-readable product name."""
    sku_labels = _sku_label_map(db)
    items = {}
    for line in collected_fields.get("move_lines", []) or []:
        label = _sku_label(sku_labels, line.get("sku_code", "?"))
        src = line.get("source_boxes_per_pallet", "?")
        tgt = line.get("target_boxes_per_pallet", "?")
        count = line.get("box_count_moved", "?")
        items[label] = f"从 {src}/托 移动 {count} 箱到 {tgt}/托"
    return [{"label": "库存调拨明细", "type": "kv", "items": items}]


def _recount_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """
    recount_storage — must show the COMPUTED DIFF against current balances,
    not the raw snapshot the warehouseman typed in.
    """
    from models.uchoice import UchoiceStorage

    sku_labels = _sku_label_map(db)
    warehouse_code = collected_fields.get("warehouse_code")
    reported = {
        (l["sku_code"], l["boxes_per_pallet"]): l["pallet_count"]
        for l in collected_fields.get("inventory_lines", []) or []
    }
    current_rows = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
    current = {(r.sku_code, r.boxes_per_pallet): r.pallet_count for r in current_rows}

    diff_items = {}
    for key in sorted(set(reported) | set(current)):
        sku, bpp = key
        before = current.get(key, 0)
        after = reported.get(key, 0)
        delta = after - before
        if delta != 0:
            sign = "+" if delta > 0 else ""
            label = _sku_label(sku_labels, sku)
            diff_items[f"{label} @ {bpp}/托"] = f"{before} → {after}（{sign}{delta}）"

    if not diff_items:
        diff_items["（无变化）"] = ""

    return [{"label": f"{warehouse_code} 库存盘点差异", "type": "kv", "items": diff_items}]


def _address_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    """upsert_address — must state create-vs-update mode explicitly."""
    mode = "更新" if collected_fields.get("matched_address_id") else "新增"
    items = {_field_label(k): v for k, v in collected_fields.items() if k != "matched_address_id"}
    return [{"label": f"您正在{mode}此地址", "type": "kv", "items": items}]


def _default_sections_builder(collected_fields: dict, db: DBSession) -> list[dict]:
    items = {_field_label(k): v for k, v in collected_fields.items()}
    return [{"label": "详情", "type": "kv", "items": items}]


CONFIRMATION_BUILDERS: dict[str, Callable[[dict, DBSession], list[dict]]] = {
    "fedex_label":              _label_sections_builder,
    "ups_label":                _label_sections_builder,
    "uchoice_inbound_request":  _inbound_sections_builder,
    "uchoice_outbound_request": _outbound_sections_builder,
    "adjust_storage":           _adjust_sections_builder,
    "recount_storage":          _recount_sections_builder,
    "move_storage":             _move_sections_builder,
    "upsert_address":           _address_sections_builder,
}


def build_sections(service_type_name: str, collected_fields: dict, db: DBSession) -> list[dict]:
    builder = CONFIRMATION_BUILDERS.get(service_type_name, _default_sections_builder)
    return builder(collected_fields, db)
