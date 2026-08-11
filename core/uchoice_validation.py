"""Reusable validation primitives for AI-supplied U-Choice line items."""

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session as DBSession

from models.uchoice import UchoiceSku


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic validation failure with a stable field path."""

    path: str
    code: str
    message: str


def validate_sku_lines(
    lines,
    *,
    field_name: str,
    db: DBSession,
    duplicate_key_fields: tuple[str, ...] | None = None,
) -> list[ValidationIssue]:
    """Validate invariant line/SKU facts shared by every U-Choice service.

    Service-specific stock, quantity, and snapshot semantics intentionally do
    not live here. ``duplicate_key_fields`` lets a caller opt into uniqueness
    for its own bucket identity without making that policy universal.
    """

    if not isinstance(lines, list):
        return [ValidationIssue(field_name, "not_array", "商品明细必须是列表。")]

    catalog_skus = {row.sku_code for row in db.query(UchoiceSku).all()}
    issues: list[ValidationIssue] = []
    seen: dict[tuple, int] = {}

    for index, line in enumerate(lines):
        line_path = f"{field_name}[{index}]"
        if not isinstance(line, dict):
            issues.append(
                ValidationIssue(line_path, "not_object", f"第 {index + 1} 项商品明细格式无效。")
            )
            continue

        sku_code = line.get("sku_code")
        sku_path = f"{line_path}.sku_code"
        if not isinstance(sku_code, str) or not sku_code.strip():
            issues.append(
                ValidationIssue(sku_path, "missing", f"第 {index + 1} 项商品缺少有效的商品编号。")
            )
        elif sku_code not in catalog_skus:
            issues.append(
                ValidationIssue(sku_path, "unknown", f"第 {index + 1} 项商品无法在商品目录中识别。")
            )

        if duplicate_key_fields:
            key = tuple(line.get(name) for name in duplicate_key_fields)
            if all(value is not None for value in key):
                if key in seen:
                    issues.append(
                        ValidationIssue(
                            line_path,
                            "duplicate",
                            f"第 {index + 1} 项商品明细与第 {seen[key] + 1} 项重复。",
                        )
                    )
                else:
                    seen[key] = index

    return issues


def format_validation_issues(issues: Iterable[ValidationIssue]) -> str | None:
    """Render issues as one targeted, deterministic pre-confirmation error."""

    messages = [issue.message for issue in issues]
    if not messages:
        return None
    return "请修正以下商品信息：" + "；".join(messages)
