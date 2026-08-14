"""
Shared pre-persistence field sanitization: both core/workflow_engine.py
(Smart Robot) and
core/kefu_turn_apply.py (Kefu) call this before merging AI-extracted fields
into persisted collected_fields. Previously defined inside workflow_engine.py
with kefu_turn_apply.py reaching into it via a private cross-module import;
moved here so neither channel privately depends on the other's orchestration
module. The public entry point is sanitize_extracted_fields_before_persistence
(no leading underscore) -- kefu_turn_apply.py imports that name directly, so
it no longer looks like it's reaching into another module's private
internals. workflow_engine.py imports it under its old private name as a
compatibility alias so every existing monkeypatch target
(`workflow_engine._sanitize_extracted_fields_before_persistence` etc.) keeps
working unchanged.
"""
from sqlalchemy.orm import Session as DBSession

_SKU_LINES_FIELD_BY_SERVICE = {
    # All listed services share the same catalog boundary before model output
    # can merge into
    # persisted collected_fields. Their service-specific pre-confirm checks
    # remain as defense in depth.
    "uchoice_outbound_request": "sku_lines",
    "uchoice_inbound_request": "sku_lines",
    "confirm_inbound_completion": "received_lines",
    "confirm_outbound_completion": "fulfillment_lines",
    "adjust_storage": "adjustment_lines",
    "move_storage": "move_lines",
    "recount_storage": "inventory_lines",
}


def sanitize_extracted_fields_before_persistence(service_name: str, extracted_fields: dict, db: DBSession, group_id: str | None = None) -> dict:
    """
    Pre-confirm validators run only immediately before confirmation. The
    primary boundary must sit
    before model output ever merges into persisted collected_fields, since
    otherwise invalid state is still stored and can be re-serialized into a
    later turn's prompt. This is that boundary for the one field class this
    session's incidents were actually about: sku_lines.

    Drops (does not persist) individual line items that fail
    core.uchoice_validation.validate_sku_lines -- missing/invalid sku_code,
    or a non-dict line -- rather than rejecting the whole merge, so a
    message with one valid line and one fabricated one still saves the
    valid one, preserving valid progress from mixed-line input. The pre-confirm
    validators remain as defense in depth for anything this doesn't cover.
    """
    if service_name == "role_change":
        return _sanitize_role_change_fields_before_persistence(extracted_fields, db, group_id)

    field_name = _SKU_LINES_FIELD_BY_SERVICE.get(service_name)
    if not field_name or field_name not in (extracted_fields or {}):
        return extracted_fields

    from core.uchoice_validation import validate_sku_lines

    lines = extracted_fields[field_name]
    if not isinstance(lines, list):
        # A malformed non-list shape (string, dict, null, etc.) must not pass
        # through unchanged and
        # get persisted as-is. Omit the whole field instead of merging
        # garbage -- collected_fields simply won't have field_name at all
        # this turn, which _outbound_required_fields_present and the
        # pre-confirm validators already correctly treat as "not yet
        # collected," rather than letting a non-list value sit in state and
        # potentially crash something that assumes it's iterable.
        print(f"[workflow] dropped malformed {field_name!r} (not a list: {type(lines).__name__}) "
              f"before persistence for {service_name!r}", flush=True)
        return {k: v for k, v in extracted_fields.items() if k != field_name}

    bad_indices = set()
    for issue in validate_sku_lines(lines, field_name=field_name, db=db):
        # issue.path looks like "sku_lines[2]" or "sku_lines[2].sku_code"
        prefix = f"{field_name}["
        if issue.path.startswith(prefix):
            try:
                bad_indices.add(int(issue.path[len(prefix):].split("]", 1)[0]))
            except ValueError:
                continue

    if not bad_indices:
        return extracted_fields

    cleaned = [line for i, line in enumerate(lines) if i not in bad_indices]
    print(f"[workflow] dropped {len(bad_indices)} invalid sku_lines entr{'y' if len(bad_indices)==1 else 'ies'} "
          f"before persistence for {service_name!r} (missing/unknown sku_code or malformed line)", flush=True)
    return {**extracted_fields, field_name: cleaned}


def _sanitize_role_change_fields_before_persistence(extracted_fields: dict, db: DBSession, group_id: str | None) -> dict:
    """
    target_openid is a candidate-backed identifier like sku_code:
    accept it only if it names a current group_member of this group. Accept
    new_role only if it's in the server allowlist (core.uchoice_constants
    .ASSIGNABLE_ROLE_NAMES -- an explicit allowlist, not "anything but
    pending", so a future internal role can't be exposed by omission).
    Invalid values are omitted individually, not merged, so an otherwise
    valid turn's other fields still persist (same "preserve valid progress"
    pattern as the sku_lines sanitizer above).
    """
    if not extracted_fields:
        return extracted_fields

    from models.group import GroupMember
    from models.kefu import KefuStaff
    from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES
    from core.role_identity import parse_target_identity

    result = dict(extracted_fields)

    # Dispatch on the tagged identity kind, never by probing for a matching
    # raw string; identifiers can collide across GroupMember and kefu_staff.
    target_openid = result.get("target_openid")
    if target_openid is not None:
        identity = parse_target_identity(target_openid)
        target_exists = False
        if identity is not None and group_id:
            if identity.kind == "kefu":
                target_exists = db.query(KefuStaff).filter_by(
                    staff_id=identity.key, group_id=group_id
                ).first() is not None
            else:
                target_exists = db.query(GroupMember).filter_by(
                    wechat_openid=identity.key, group_id=group_id
                ).first() is not None
        if not target_exists:
            print(f"[workflow] dropped fabricated role_change.target_openid "
                  f"before persistence: {target_openid!r} is not a member of this group", flush=True)
            result.pop("target_openid", None)

    new_role = result.get("new_role")
    if new_role is not None and new_role not in ASSIGNABLE_ROLE_NAMES:
        print(f"[workflow] dropped invalid role_change.new_role before persistence: {new_role!r}", flush=True)
        result.pop("new_role", None)

    return result
