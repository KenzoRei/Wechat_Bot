"""
Which warehouses a 费用报告 (view_invoice) request covers (invoice-inventory
plan, Phase 3; decision D2).

The warehouse is optional. Explicit codes win; otherwise the request covers
every warehouse the caller may see:
- an unscoped role (admin, accountant, ...) gets every VALID_WAREHOUSE_CODES;
- a warehouse-scoped role gets its assigned warehouse_codes;
- a warehouse-scoped role with no assignment is rejected (fail-closed, the
  same rule as core.role_policy.check_warehouse_scope), never given "all".

The list is resolved once, when the request runs (both channels call
resolve_invoice_warehouses right before pre-confirm validation), and saved
into collected_fields["warehouse_codes"]. Later turns re-check that frozen
list against the caller's current access (core.kefu_case_adapter.
_authorize_case); they never re-expand it.

Accepts the legacy single `warehouse_code` field too (sessions started
before V38, or an AI that still fills it).
"""
from core import role_policy
from core.uchoice_constants import VALID_WAREHOUSE_CODES

# What a user (or the AI copying them) may say for "every warehouse".
_ALL_TOKENS = frozenset({"ALL", "全部", "所有", "所有仓库", "全部仓库"})


class InvalidWarehouseSelection(ValueError):
    """The warehouse field has a shape or mix that can't be read safely."""


_MALFORMED = "无法识别所选仓库，请用仓库代码说明（如 JFK、DE），或说明「全部仓库」。"
_MIXED_ALL = "不能同时选择「全部仓库」和具体仓库，请只选其一。"


def _split(value: str) -> list[str]:
    return value.replace("，", ",").replace("、", ",").split(",")


def requested_invoice_warehouses(fields: dict) -> list[str]:
    """
    The codes the request names, de-duplicated and sorted; [] means "not
    stated" or stated as all. Unknown codes are left for the pre-confirm
    validator, so the user gets an error naming them.

    Raises InvalidWarehouseSelection for anything that must not quietly
    become "all" (Codex code audit #3 and round 2 #2): a non-list/non-string
    value, a non-string entry, "all" mixed with specific codes, or a supplied
    value that names nothing ("," / [""] / "  ").
    """
    raw = fields.get("warehouse_codes")
    if raw is None or raw == "" or raw == []:
        legacy = fields.get("warehouse_code")
        if legacy is None or legacy == "":
            return []
        if not isinstance(legacy, str):
            raise InvalidWarehouseSelection(_MALFORMED)
        raw = [legacy]          # the legacy field is one code, never a list
    elif isinstance(raw, str):
        raw = _split(raw)
    elif not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise InvalidWarehouseSelection(_MALFORMED)

    codes = [c.strip() for c in raw if c.strip()]
    if not codes:
        raise InvalidWarehouseSelection(_MALFORMED)   # supplied, but names no warehouse
    all_tokens = [c for c in codes if c.upper() in _ALL_TOKENS or c in _ALL_TOKENS]
    if all_tokens:
        if len(all_tokens) != len(codes):
            raise InvalidWarehouseSelection(_MIXED_ALL)
        return []
    return sorted(dict.fromkeys(codes))


def accessible_invoice_warehouses(context: dict) -> tuple[list[str], str | None]:
    """Every warehouse the caller may see, or ([], denial message)."""
    allowed = context.get("warehouse_codes")
    if allowed:
        return sorted(dict.fromkeys(allowed)), None
    denial = role_policy.check_warehouse_scope(context.get("role"), allowed, None)
    if denial:
        return [], denial
    return sorted(VALID_WAREHOUSE_CODES), None


def resolve_invoice_warehouses(context: dict, fields: dict) -> tuple[dict, str | None]:
    """
    Returns (updated fields, None) with warehouse_codes set to the effective
    list, or (fields unchanged, message) for a denial or an unreadable
    selection -- the session stays open, so the user can correct it.
    Idempotent: an already resolved list is kept as is.
    """
    try:
        codes = requested_invoice_warehouses(fields)
    except InvalidWarehouseSelection as exc:
        return fields, str(exc)
    if not codes:
        codes, denial = accessible_invoice_warehouses(context)
        if denial:
            return fields, denial
    return {**fields, "warehouse_codes": codes}, None


def invoice_warehouses_for_execution(context: dict, fields: dict) -> list[str]:
    """
    The list a running invoice uses: the one resolved before validation, or
    -- defensively, if a caller skipped that step -- resolved now. Never
    silently empty: a denial here raises, failing the request loudly.
    """
    resolved, denial = resolve_invoice_warehouses(context, fields)
    if denial:
        raise RuntimeError(denial)
    return resolved["warehouse_codes"]
