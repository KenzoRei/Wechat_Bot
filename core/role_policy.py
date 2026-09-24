"""
Shared, typed role-level policy declarations plus their two consumers:
assignment-time normalization/validation, and the fail-closed runtime scope
check. Per docs/architecture/decisions/adr-010-role-service-policy-
declarations.md, and per the 2026-09-15 implementation audit
(.collab/tasks/role-permission-attribute-architecture.md) that found the
first pass incomplete on three points, all addressed here:

1. Runtime enforcement (core.pre_confirm_validators._valid_caller_
   warehouse_scope, handlers.uchoice.storage_txns._require_caller_
   warehouse_scope, handlers.uchoice.record_request) previously inferred
   "unrestricted" from a bare `warehouse_codes is None`, indistinguishable
   from "scoped role, assignment data missing." check_warehouse_scope below
   is now the single fail-closed implementation both consult: a
   warehouse-scoped role with no assigned codes is REJECTED outright, not
   treated as unrestricted.
2. Assignment-time normalization was two independently-invoked functions
   with no shared declaration -- ASSIGNMENT_FIELD_POLICIES below is the one
   typed declaration (field name, label, value type, choice source for the
   admin-panel/API layer, which roles require it, how to normalize a
   candidate value), and normalize_assignment_fields() is the one shared
   entry point every assignment call site now goes through.
3. See api/admin/roles.py's grant_role_service_permission for the
   corresponding grant-impact check -- rejects a grant that would leave a
   warehouse-scoped role's already-existing, incompletely-provisioned
   assignments gaining reachability to a warehouse-touching service.

This module does NOT own the underlying role classifications themselves
(core.role_registry.WAREHOUSE_SCOPED_ROLE_NAMES / CUSTOMER_IDENTITY_ROLE_
NAMES) -- those stay in role_registry.py because other, unrelated domain
code (customer-identity resolution for label billing, e.g. handlers/label/
base.py, core/kefu_turn_apply.py, core/workflow_engine.py) consults
CUSTOMER_IDENTITY_ROLE_NAMES directly for a different concern (which
customer a caller may bill a label to on a given request, not whether a
field is required at assignment time) and must keep doing so unchanged.
Nor does it replace domain-specific behavior that isn't just "is this field
required" -- core.customer_directory.resolve_billing_customer_id's
forced-override-vs-free-selection logic stays a dedicated function, only
its "is this role required to have one" question is answered here.
"""
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from core.role_registry import CUSTOMER_IDENTITY_ROLE_NAMES, WAREHOUSE_SCOPED_ROLE_NAMES
from core.uchoice_constants import VALID_WAREHOUSE_CODES


class RoleAssignmentError(ValueError):
    """
    Raised with a machine-checkable `field` + `reason` plus whatever `info`
    a caller needs to reconstruct its own user-facing message --
    deliberately not a pre-formatted string, since call sites use different
    languages/conventions for the same underlying rejection (English
    HTTPException detail vs. Chinese conversational error text) and none of
    that formatting belongs in a shared validation module. `field` is set
    by normalize_assignment_fields (not by an individual policy's
    normalize(), which doesn't know its own field_name in isolation) --
    lets one try/except at a call site handle every field's rejection.
    """

    def __init__(self, reason: str, field: str | None = None, **info):
        self.reason = reason
        self.field = field
        self.info = info
        super().__init__(reason)


def _normalize_warehouse_codes_value(db: Session, role_name: str, raw_codes) -> list[str] | None:
    del db
    if role_name not in WAREHOUSE_SCOPED_ROLE_NAMES:
        return None
    if not isinstance(raw_codes, list):
        # Guards against e.g. a bare string here -- iterating a string
        # yields its characters, which would silently pass through the
        # comprehension below as bogus single-letter "codes" instead of
        # failing loudly.
        raise RoleAssignmentError("missing")
    cleaned = sorted({c.strip() for c in raw_codes if c and c.strip()})
    if not cleaned:
        raise RoleAssignmentError("missing")
    unknown = [c for c in cleaned if c not in VALID_WAREHOUSE_CODES]
    if unknown:
        raise RoleAssignmentError("unknown_codes", unknown=unknown)
    return cleaned


def _normalize_billing_customer_value(db: Session, role_name: str, raw_id) -> str | None:
    if role_name not in CUSTOMER_IDENTITY_ROLE_NAMES:
        return None
    customer_id = (raw_id or "").strip() if isinstance(raw_id, str) else ""
    if not customer_id:
        raise RoleAssignmentError("missing")
    from core import customer_directory
    record = customer_directory.get_customer(db, customer_id)
    if record is None:
        raise RoleAssignmentError("not_found", customer_id=customer_id)
    if record.status != "active":
        raise RoleAssignmentError("inactive", customer_id=customer_id, status=record.status)
    return customer_id


@dataclass(frozen=True)
class AssignmentFieldPolicy:
    """
    One assignment-level field's complete policy: which roles require it,
    how to normalize/validate a candidate value for a given role, and the
    descriptor metadata an admin-panel/API layer needs to render and
    require it generically. Adding the next assignment-level field means
    adding one more instance of this to ASSIGNMENT_FIELD_POLICIES below --
    not a new frozenset, a new pair of functions, and a new ad hoc boolean
    on RoleResponse, at every one of the four call sites.
    """
    field_name: str
    label: str
    value_type: str  # "multi_choice" | "text" -- the small set of known admin-panel controls this system actually has; a genuinely new value_type still needs its own control, same as ADR-010 always intended for domain-specific pickers
    choice_source: str | None  # e.g. "warehouse_codes" -- names a server-exposed choice list; None for free text
    applies_to_roles: frozenset  # role names for which this field is required
    normalize: Callable[[Session, str, object], object]

    def applies_to(self, role_name: str) -> bool:
        return role_name in self.applies_to_roles


WAREHOUSE_CODES_POLICY = AssignmentFieldPolicy(
    field_name="warehouse_codes",
    label="Warehouse(s)",
    value_type="multi_choice",
    choice_source="warehouse_codes",
    applies_to_roles=WAREHOUSE_SCOPED_ROLE_NAMES,
    normalize=_normalize_warehouse_codes_value,
)

BILLING_CUSTOMER_POLICY = AssignmentFieldPolicy(
    field_name="billing_customer_id",
    label="Billing customer",
    value_type="text",
    choice_source=None,
    applies_to_roles=CUSTOMER_IDENTITY_ROLE_NAMES,
    normalize=_normalize_billing_customer_value,
)

# The one typed declaration -- every assignment-level policy field is one
# entry here. Order matters only for which field's error surfaces first
# out of normalize_assignment_fields when more than one is invalid at once.
ASSIGNMENT_FIELD_POLICIES: tuple[AssignmentFieldPolicy, ...] = (
    WAREHOUSE_CODES_POLICY,
    BILLING_CUSTOMER_POLICY,
)


def requires_warehouse_scope(role_name: str) -> bool:
    return WAREHOUSE_CODES_POLICY.applies_to(role_name)


def requires_billing_customer(role_name: str) -> bool:
    return BILLING_CUSTOMER_POLICY.applies_to(role_name)


def field_descriptors_for_role(role_name: str) -> list[dict]:
    """
    Descriptor list for every assignment-level field this role requires --
    what api/admin/roles.py exposes on GET /admin/roles instead of one
    dedicated boolean per field. The admin panel derives its per-field
    show/require logic from field_name membership in this list; it does not
    need updating to learn about a role newly requiring an existing field
    (only a genuinely new field needs a new client-side control, per the
    module docstring).
    """
    return [
        {
            "field_name": policy.field_name,
            "label": policy.label,
            "value_type": policy.value_type,
            "choice_source": policy.choice_source,
        }
        for policy in ASSIGNMENT_FIELD_POLICIES
        if policy.applies_to(role_name)
    ]


def normalize_assignment_fields(db: Session, role_name: str, raw_values: dict) -> dict:
    """
    The one shared assignment-validation entry point (ADR-010 step 1).
    `raw_values` is a dict of candidate values keyed by field_name (e.g.
    {"warehouse_codes": [...], "billing_customer_id": "..."}) -- whichever
    the caller has already decided on (the request body's value, or the
    existing stored value carried forward on a partial update; this
    function does no merging itself).

    Returns the dict of values to actually persist, one entry per declared
    field: normalized/validated if `role_name` requires that field, or
    explicitly None (clearing it) if it doesn't -- callers should assign
    every key of the result unconditionally, so a role change away from a
    scoped/identity role reliably clears a stale value from the previous
    role.

    Raises RoleAssignmentError (with `.field` set to whichever field
    failed) on the first invalid required field.
    """
    result = {}
    for policy in ASSIGNMENT_FIELD_POLICIES:
        try:
            result[policy.field_name] = policy.normalize(db, role_name, raw_values.get(policy.field_name))
        except RoleAssignmentError as exc:
            exc.field = policy.field_name
            raise
    return result


# Backward-compatible single-field wrappers, for call sites that only ever
# touch one field at a time (e.g. the REST admin API's "role unchanged,
# only this field was patched" branches, where the other field is
# necessarily left alone). Both raise RoleAssignmentError exactly as
# normalize_assignment_fields does for the same field.

def normalize_warehouse_codes(role_name: str, raw_codes) -> list[str] | None:
    return _normalize_warehouse_codes_value(None, role_name, raw_codes)


def validate_billing_customer_id(db: Session, role_name: str, raw_id) -> str | None:
    return _normalize_billing_customer_value(db, role_name, raw_id)


MISSING_WAREHOUSE_SCOPE_MESSAGE = "您的仓库权限尚未配置，请联系管理员后再试。"
OUT_OF_WAREHOUSE_SCOPE_MESSAGE = "该仓库不在您的权限范围内。"


def check_warehouse_scope(role_name: str, allowed_codes: list[str] | None, requested_code: str | None) -> str | None:
    """
    The one fail-closed runtime warehouse-scope check -- consulted by
    core.pre_confirm_validators._valid_caller_warehouse_scope,
    core.pre_confirm_validators._valid_upsert_address_warehouse_scope,
    handlers.uchoice.storage_txns._require_caller_warehouse_scope, and
    handlers.uchoice.record_request.RecordUchoiceRequestHandler. Replaces
    each of their previous `if allowed_codes is None: return None` --
    that treated "role doesn't need scope" and "role needs scope but
    warehouse_codes wasn't actually assigned" identically, both as
    unrestricted. Now:

    - If `allowed_codes` is a non-empty assignment: always enforced against
      `requested_code` regardless of `role_name` -- assignment-time
      normalization only ever populates warehouse_codes for a scoped role
      in the first place, so a real assignment is authoritative on its own
      and this doesn't need `role_name` to be reliably present for the
      common case (many call sites carry a `context` built before "role"
      became a tracked key; only the fail-closed branch below is new and
      actually needs it).
    - If `allowed_codes` is empty/None: allowed if `role_name` genuinely
      isn't warehouse-scoped (admin/customer/accountant/label_agent/...,
      unchanged from before); rejected outright if it IS warehouse-scoped
      -- a scoped role with no assignment data is a misprovisioning, not a
      green light, regardless of whether a specific warehouse was even
      named this turn.
    """
    if allowed_codes:
        if requested_code and requested_code not in allowed_codes:
            return OUT_OF_WAREHOUSE_SCOPE_MESSAGE
        return None
    if requires_warehouse_scope(role_name):
        return MISSING_WAREHOUSE_SCOPE_MESSAGE
    return None


# Service-definition metadata (layer 2 of the original three-layer design
# discussion): service_type names whose pre-confirm validator or execution
# handler consults check_warehouse_scope above -- see
# core.pre_confirm_validators.PRE_CONFIRM_VALIDATORS (uchoice_outbound_
# request, uchoice_inbound_request, adjust_storage, move_storage,
# recount_storage, view_storage, view_storage_history, view_invoice,
# upsert_address) and handlers.uchoice.storage_txns's
# ApplyInboundStorageHandler/ApplyOutboundStorageHandler (confirm_inbound_
# completion/confirm_outbound_completion, added in the same 2026-09-15 audit
# fix as check_warehouse_scope's fail-closed behavior). Used only by
# warehouse_grant_impact below; kept as a plain declared set here rather
# than inferred from PRE_CONFIRM_VALIDATORS's composition, so this module
# doesn't import core.pre_confirm_validators (which would be circular --
# that module already imports this one).
WAREHOUSE_SCOPED_SERVICE_NAMES = frozenset({
    "uchoice_outbound_request",
    "uchoice_inbound_request",
    "adjust_storage",
    "move_storage",
    "recount_storage",
    "view_storage",
    "view_storage_history",
    "view_invoice",
    "upsert_address",
    "confirm_inbound_completion",
    "confirm_outbound_completion",
    # Batch completion re-runs the single completion's warehouse-scope
    # checks per target (handlers/uchoice/complete_batch.py).
    "confirm_inbound_completion_batch",
    "confirm_outbound_completion_batch",
})


def warehouse_scope_compliance_gap(db: Session, role_name: str) -> dict | None:
    """
    Counts existing GroupMember/KefuStaff holders of `role_name` with no
    warehouse_codes assigned (null or empty array -- Postgres quirk:
    array_length() of BOTH returns NULL, so one predicate catches both
    without a separate "= '{}'" check). Returns None if every existing
    holder already has codes.

    Raises ValueError if `role_name` is not currently declared in
    WAREHOUSE_SCOPED_ROLE_NAMES -- this function checks compliance for an
    ALREADY-declared policy, and must never silently report "no gap" for a
    role that was simply never checked because it isn't (yet) declared
    scoped at all (2026-09-15 audit, third round: scripts/check_role_
    policy_impact.py exited 0 for 'accountant' -- not warehouse-scoped --
    without ever querying its assignments, a false pass for exactly the
    "role about to become warehouse-scoped" case the script exists to
    check). Add the role to WAREHOUSE_SCOPED_ROLE_NAMES in your working
    tree first, THEN run the compliance check against the target database,
    THEN deploy -- this function enforces that ordering by refusing to
    silently skip step 1.

    This is the read-only compliance check behind two consumers (ADR-010
    decision 4):
    - warehouse_grant_impact below, run automatically by api/admin/roles.py
      before a new RoleServicePermission grant -- guards its own call with
      the WAREHOUSE_SCOPED_ROLE_NAMES membership check itself, so a normal
      grant to a genuinely unscoped role never hits this ValueError.
    - scripts/check_role_policy_impact.py, run MANUALLY as a required
      pre-deploy gate before merging any change to
      WAREHOUSE_SCOPED_ROLE_NAMES itself -- a code-level classification
      change has no admin-API call site to hook an automatic check onto,
      unlike a grant, so this half of decision 4 is a documented manual
      step rather than an enforced one.
    """
    if role_name not in WAREHOUSE_SCOPED_ROLE_NAMES:
        raise ValueError(
            f"'{role_name}' is not declared in core.role_registry."
            f"WAREHOUSE_SCOPED_ROLE_NAMES -- add it there first (in the "
            f"working tree you intend to deploy), then re-run this check."
        )

    from sqlalchemy import or_, func
    from models.group import GroupMember
    from models.kefu import KefuStaff
    from models.role import Role

    role = db.query(Role).filter_by(name=role_name).first()
    if role is None:
        return None

    incomplete = lambda model: or_(model.warehouse_codes.is_(None), func.array_length(model.warehouse_codes, 1).is_(None))
    member_count = db.query(GroupMember).filter(GroupMember.role_id == role.role_id).filter(incomplete(GroupMember)).count()
    staff_count = db.query(KefuStaff).filter(KefuStaff.role_id == role.role_id).filter(incomplete(KefuStaff)).count()
    if not member_count and not staff_count:
        return None
    return {"member_count": member_count, "staff_count": staff_count}


def warehouse_grant_impact(db: Session, role_name: str, service_name: str) -> dict | None:
    """
    Grant-impact preflight: if granting `service_name` to `role_name` would
    give an already-existing, incompletely-provisioned assignment
    reachability to a warehouse-scoped service, returns
    warehouse_scope_compliance_gap's result so api/admin/roles.py can
    reject the grant instead of letting it through. Such an assignment
    would in fact still be denied at runtime by check_warehouse_scope's
    fail-closed behavior above -- this isn't closing a security hole, it's
    catching a remediation gap before an admin ships a grant that
    immediately, silently locks out some of the role's existing holders.
    Returns None if `role_name` isn't currently warehouse-scoped or
    `service_name` isn't warehouse-scoped -- checked here (not left to
    warehouse_scope_compliance_gap's own guard) so that granting an
    unrelated service to a genuinely unscoped role (e.g. accountant) is
    always a normal no-op call, never the ValueError that function now
    raises for a role that isn't declared scoped at all.
    """
    if role_name not in WAREHOUSE_SCOPED_ROLE_NAMES or service_name not in WAREHOUSE_SCOPED_SERVICE_NAMES:
        return None
    return warehouse_scope_compliance_gap(db, role_name)
