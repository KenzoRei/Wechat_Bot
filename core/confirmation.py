def build_confirmation_message(
    service_type_name: str,
    collected_fields: dict,
    serial_number: str,
    confirmation_note: str | None = None
) -> str:
    """
    Generates a fixed Chinese confirmation template.
    Called when all required fields are collected (all_fields_collected=True).

    The serial number is shown here for the first time — the request_log row
    must be created before calling this function so the serial exists.

    confirmation_note: optional disclaimer from service_type.confirmation_note.
    If None, no note section is appended.

    The user confirms by replying "确认" and cancels by replying "取消".
    No AI is involved — the template is deterministic and auditable.
    """
    lines = [
        "请确认以下寄件信息：",
        f"申请编号：{serial_number}",
        f"服务类型：{_service_display_name(service_type_name)}",
        "─────────────────",
    ]

    for field_key, field_value in collected_fields.items():
        label = _field_label(field_key)
        lines.append(f"{label}：{field_value}")

    lines += [
        "─────────────────",
        '回复“确认”提交申请，或“取消”放弃。',
    ]

    if confirmation_note:
        lines += [
            "",
            f"📌 注意：{confirmation_note}",
        ]

    return "\n".join(lines)


# ── private helpers ───────────────────────────────────────────────────────────

def _service_display_name(service_type_name: str) -> str:
    """Maps internal service type name to a human-readable Chinese label."""
    _map = {
        "fedex_label": "FedEx 快递标签",
        "ups_label":   "UPS 快递标签",
    }
    return _map.get(service_type_name, service_type_name)


def _field_label(field_key: str) -> str:
    """Maps collected_fields keys to Chinese display labels."""
    _map = {
        "recipient_name": "收件人姓名",
        "street":         "街道地址",
        "city":           "城市",
        "state":          "州/省",
        "zip":            "邮编",
        "weight_lbs":     "重量（磅）",
        "service_level":  "服务等级",
        "package_type":   "包裹类型",
        "reference":      "参考编号",
    }
    return _map.get(field_key, field_key)
