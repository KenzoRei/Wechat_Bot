def build_confirmation_message(
    service_type_name: str,
    collected_fields: dict,
    serial_number: str,
    confirmation_note: str | None = None
) -> str:
    """
    Generates a fixed Chinese confirmation template (rendered as markdown in WeChat).
    Called when all required fields are collected (all_fields_collected=True).
    """
    shipper_fields = {k: v for k, v in collected_fields.items() if k.startswith("shipper_")}
    recipient_fields = {k: v for k, v in collected_fields.items() if k.startswith("recipient_")}
    other_fields = {
        k: v for k, v in collected_fields.items()
        if not k.startswith("shipper_") and not k.startswith("recipient_")
    }

    lines = [
        f"**请确认以下寄件信息**",
        f"申请编号：{serial_number}",
        f"服务类型：{_service_display_name(service_type_name)}",
        "",
        "**发件人**",
    ]

    for key, val in shipper_fields.items():
        label = _field_label(key)
        lines.append(f"- {label}：{val}")

    lines += ["", "**收件人**"]
    for key, val in recipient_fields.items():
        label = _field_label(key)
        lines.append(f"- {label}：{val}")

    if other_fields:
        lines += ["", "**包裹信息**"]
        for key, val in other_fields.items():
            label = _field_label(key)
            lines.append(f"- {label}：{val}")

    lines += [
        "",
        '回复 **确认** 提交申请，或 **取消** 放弃。',
    ]

    if confirmation_note:
        lines += ["", f"> 注意：{confirmation_note}"]

    return "\n".join(lines)


# ── private helpers ───────────────────────────────────────────────────────────

def _service_display_name(service_type_name: str) -> str:
    _map = {
        "fedex_label": "FedEx 快递标签",
        "ups_label":   "UPS 快递标签",
    }
    return _map.get(service_type_name, service_type_name)


def _field_label(field_key: str) -> str:
    _map = {
        # shipper fields
        "shipper_name":      "姓名",
        "shipper_corp_name": "公司",
        "shipper_phone":     "电话",
        "shipper_street":    "地址",
        "shipper_city":      "城市",
        "shipper_state":     "州/省",
        "shipper_zip":       "邮编",
        "shipper_country":   "国家",
        # recipient fields
        "recipient_name":      "姓名",
        "recipient_corp_name": "公司",
        "recipient_phone":     "电话",
        "recipient_street":    "地址",
        "recipient_city":      "城市",
        "recipient_state":     "州/省",
        "recipient_zip":       "邮编",
        "recipient_country":   "国家",
        # package fields
        "weight_lbs":      "重量（磅）",
        "service_level":   "服务等级",
        "length_in":       "长度（英寸）",
        "width_in":        "宽度（英寸）",
        "height_in":       "高度（英寸）",
        "reference_number": "参考编号",
    }
    return _map.get(field_key, field_key)
