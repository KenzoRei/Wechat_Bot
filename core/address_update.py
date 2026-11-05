"""
update_address support: editable fields, baseline snapshot, effective changes.

update_address edits ONE existing row of the shared U-Choice address book,
chosen by `matched_address_id` (picked by the AI from the injected
`address_book` list and re-validated here against the database).

Design rules (see .collab/tasks/address-add-update-split-plan*.md):
- Only the fields in EDITABLE_FIELDS can change; customer_id (and anything
  else the model might emit) is never bound.
- charge_type / warehouse_code are ADMIN_ONLY *when they actually change*
  (echoing the current value is not a change).
- `_baseline` freezes the values the user is shown on the confirmation card.
  Execution compares the live row to it; if another session changed the row
  in between, the update is refused (approving A->B must never silently
  overwrite C). Internal "_" keys can never come from the AI.
- Omitted field = keep. Explicit blank string = clear (optional fields
  only); addr can never be cleared.
"""
import uuid

EDITABLE_FIELDS = ("company_name", "addr", "contact", "phone", "note", "charge_type", "warehouse_code")
ADMIN_ONLY_FIELDS = ("charge_type", "warehouse_code")
CLEARABLE_FIELDS = ("company_name", "contact", "phone", "note")

STALE_MESSAGE = "该地址在您确认之前已被他人修改，为避免覆盖，请重新发起地址更新。"
FIELD_LABELS = {
    "company_name": "公司", "addr": "地址", "contact": "联系人", "phone": "电话",
    "note": "备注", "charge_type": "计费类型", "warehouse_code": "所属仓库",
}


def snapshot(row) -> dict:
    return {f: getattr(row, f, None) for f in EDITABLE_FIELDS}


def parse_address_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def load_target(db, fields: dict, *, lock: bool = False):
    """The addressed row, or None. lock=True takes a row lock (execution time)."""
    from models.uchoice import UchoiceAddress

    address_id = parse_address_id(fields.get("matched_address_id"))
    if address_id is None:
        return None
    query = db.query(UchoiceAddress).filter_by(address_id=address_id)
    if lock:
        # populate_existing: with_for_update() locks the row but does NOT
        # refresh an already identity-mapped instance, so the stale/no-op/
        # admin-only checks could otherwise run on cached old values.
        if hasattr(query, "populate_existing"):
            query = query.populate_existing()
        if hasattr(query, "with_for_update"):
            query = query.with_for_update()
    return query.first()


def baseline_patch(db, fields: dict) -> dict:
    """Fields to merge into the session so `_baseline` matches the chosen row.

    Set once per chosen address; re-set only when the user changes WHICH
    address is being edited. A later turn never refreshes it, so the card the
    user approved stays comparable to the live row.
    """
    address_id = str(fields.get("matched_address_id") or "")
    if not address_id or db is None:
        return {}
    if fields.get("_baseline") is not None and fields.get("_baseline_id") == address_id:
        return {}
    row = load_target(db, fields)
    if row is None:
        return {}
    return {"_baseline": snapshot(row), "_baseline_id": address_id}


def _normalise(field: str, value):
    """(is_present, normalised value). Blank string on a clearable field = clear."""
    if value is None:
        return False, None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return (field in CLEARABLE_FIELDS), None
    return True, value


def effective_changes(current: dict, fields: dict) -> dict:
    """{field: (old, new)} for editable fields whose requested value differs."""
    changes = {}
    for field in EDITABLE_FIELDS:
        if field not in fields:
            continue
        present, new = _normalise(field, fields[field])
        if not present:
            continue
        old = current.get(field)
        if (old or None) != (new or None):
            changes[field] = (old, new)
    return changes


_MAX_LENGTHS = {"company_name": 200, "contact": 100, "phone": 50, "note": 2000, "addr": 500}


def validate_values(fields: dict) -> str | None:
    """Domain checks on the SUPPLIED editable values (before confirmation and
    again at execution): types, lengths, charge_type and warehouse_code
    membership. An explicit empty string (clearing) is allowed where
    clearing is allowed; None means "not supplied"."""
    from core.address_suggestion import valid_charge_type
    from core.uchoice_constants import VALID_WAREHOUSE_CODES

    for field in EDITABLE_FIELDS:
        if field not in fields or fields[field] is None:
            continue
        value = fields[field]
        if not isinstance(value, str):
            return f"{FIELD_LABELS[field]}格式不正确，请重新提供。"
        limit = _MAX_LENGTHS.get(field)
        if limit and len(value.strip()) > limit:
            return f"{FIELD_LABELS[field]}过长（最多 {limit} 个字符），请缩短后重新提供。"
    charge_type = fields.get("charge_type")
    if charge_type not in (None, "") and valid_charge_type(charge_type) is None:
        return "计费类型无效，请选择：短途配送、配送、卡车转仓或自提。"
    if charge_type == "":
        return "计费类型不能清空。"
    warehouse = fields.get("warehouse_code")
    if warehouse not in (None, "") and warehouse not in VALID_WAREHOUSE_CODES:
        return f"未知仓库：{warehouse}。请提供有效的仓库代码（{'、'.join(sorted(VALID_WAREHOUSE_CODES))}）。"
    if warehouse == "":
        return "所属仓库不能清空。"
    return None


def blank_required_field(fields: dict) -> str | None:
    """addr cannot be cleared; returns an error message if the model tried."""
    if "addr" in fields and isinstance(fields["addr"], str) and fields["addr"].strip() == "":
        return "地址不能为空；如不想修改地址，请不要提供该字段。"
    return None


def stale_against_baseline(row, fields: dict) -> bool:
    baseline = fields.get("_baseline")
    if not isinstance(baseline, dict):
        return False
    live = snapshot(row)
    return any((baseline.get(f) or None) != (live.get(f) or None) for f in EDITABLE_FIELDS)


def post_update_values(row, changes: dict) -> dict:
    values = snapshot(row)
    for field, (_old, new) in changes.items():
        values[field] = new
    return values


def render_change(old, new) -> str:
    return f"{old if old else '（空）'} → {new if new else '（清空）'}"
