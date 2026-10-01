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


def requested_invoice_warehouses(fields: dict) -> list[str]:
    """
    The codes the request names, de-duplicated and sorted; [] means "not
    stated" (or stated as all). Not validated here -- unknown codes are the
    pre-confirm validator's job, so the user gets a clear error naming them.
    """
    raw = fields.get("warehouse_codes")
    if isinstance(raw, str):
        raw = [part for part in raw.replace("，", ",").replace("、", ",").split(",")]
    if not isinstance(raw, list) or not raw:
        legacy = fields.get("warehouse_code")
        raw = [legacy] if legacy else []
    codes = [str(c).strip() for c in raw if c is not None and str(c).strip()]
    if any(c.upper() in _ALL_TOKENS or c in _ALL_TOKENS for c in codes):
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
    list, or (fields unchanged, denial message). Idempotent: an already
    resolved list is kept as is.
    """
    codes = requested_invoice_warehouses(fields)
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
