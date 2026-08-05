"""
Shared section-rendering primitive used by both core/confirmation.py
(pre-execution summary) and core/result_message.py (post-execution outcome)
— same {label, type, items} shape, one rendering loop, not duplicated.
"""


def render_sections(sections: list[dict]) -> list[str]:
    """
    Renders a list of {"label": str, "type": "kv"|"list"|"raw", "items": dict|list}
    into markdown lines (no header/footer — callers add those).

    - "kv"/"list": bulleted, with a blank-line spacer before the section
      (plus a bold heading if "label" is set).
    - "raw": items are already fully-formatted lines — no bullet, no
      spacer, no heading. For legacy flat-format messages (e.g. FedEx/UPS's
      original layout) that shouldn't gain bullets/spacing they never had.
    """
    lines = []
    for section in sections:
        if section["type"] == "raw":
            for item in section["items"]:
                lines.append(item)
            continue

        if section.get("label"):
            lines += ["", f"**{section['label']}**"]
        else:
            lines.append("")
        if section["type"] == "kv":
            for key, val in section["items"].items():
                lines.append(f"- {key}：{val}")
        else:  # "list"
            for item in section["items"]:
                lines.append(f"- {item}")
    return lines
