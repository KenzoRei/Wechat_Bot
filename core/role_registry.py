"""
Role assignability registry -- separate from models.role.Role (the real DB
table backing the role catalog). This module answers a different question:
of the roles that exist in the DB, which *names* are actually allowed to be
assigned to a person right now.

Deliberately a code-level allowlist, not a DB column on Role (e.g.
is_assignable) -- see the 2026-09 role-permission-infrastructure
discussion for the reasoning: making assignability itself admin-panel-
editable would let someone create a role, mark it assignable, grant it
services, and hand it to a real person, all without a single line of code
ever being reviewed. A brand-new role name requiring a deliberate code
change + deploy before it's usable is a cheap, meaningful check on a
decision that governs real permissions -- the same deny-by-default
philosophy already applied to group_service (a tenant needs explicit
enablement) and role_service_permission (a role needs explicit service
grants). This is one more link in that same chain, not an inconsistency.

An exclusion rule ("every role except pending") would silently expose any
future internal/system role the moment it's added -- this is an explicit
positive allowlist instead. "pending" is deliberately absent: system-
assigned only, via self-registration, never a role_change target.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AssignableRole:
    name: str
    description: str  # short human-readable purpose, for code readers -- not a substitute for Role.description in the DB


ASSIGNABLE_ROLES: tuple[AssignableRole, ...] = (
    AssignableRole("admin", "Full access to group services and admin-level actions"),
    AssignableRole("customer", "Standard requester — access limited to explicitly granted services"),
    AssignableRole("warehouseman", "Confirms inbound/outbound completions, corrects storage (adjust/recount/move)"),
    AssignableRole("accountant", "Read-only financial visibility — storage and invoice viewing"),
    AssignableRole("label_agent", "Staff — creates FedEx/UPS shipping labels on behalf of any customer named per-request"),
    AssignableRole("fedex_label_agent", "Customer — creates FedEx labels for their own bound customer account only, not UPS"),
    AssignableRole("warehouse_admin", "Makes uchoice inbound/outbound requests and confirms their completion, scoped to assigned warehouse(s)"),
)

# Kept as a plain frozenset for every existing call site's `name in
# ASSIGNABLE_ROLE_NAMES` check -- ASSIGNABLE_ROLES above is where new
# per-role metadata (e.g. a default set of services to suggest granting on
# creation) should be added going forward, without another rename/relocate.
ASSIGNABLE_ROLE_NAMES = frozenset(r.name for r in ASSIGNABLE_ROLES)

# Names a role can never be deleted under, regardless of whether it's
# currently assigned to anyone -- same code-level-allowlist philosophy as
# ASSIGNABLE_ROLES above, and for the same reason: this governs a real
# safety invariant, not something an admin-panel checkbox should control.
# "admin" per the obvious operational risk (locking everyone out of admin
# actions). "pending" is less obvious but just as real: core/kefu_
# registration.py (and core/self_registration.py) look it up by literal
# name at self-registration time -- deleting it would silently break every
# future 注册成员 registration with no clear symptom, even though no user
# row ever holds "pending" permanently (it's a transient landing role), so
# the "not assigned to any user" check alone would never catch this one.
PROTECTED_ROLE_NAMES = frozenset({"admin", "pending"})

# Roles representing an external customer's OWN identity -- these callers
# get a mandatory, fixed billing_customer_id binding (GroupMember/KefuStaff)
# that no message they send can override, and core.customer_directory.
# resolve_billing_customer_id() REJECTS them outright if that binding isn't
# set (see its docstring). Every other role (label_agent, warehouseman,
# admin, ...) is staff: they specify WHICH customer a label is for on each
# request, validated only for existence + active status, not tied to any
# personal binding, because staff legitimately act on behalf of many
# different customers across different requests.
#
# "customer" is the original, unrestricted customer role. "fedex_label_agent"
# is a narrower customer role (e.g. yestech) -- same self-identity binding
# requirement, just scoped to fedex_label only, not the full uchoice_*
# service set "customer" gets. Getting this categorization wrong is a real
# security issue, not just a premature-access one: a role mistakenly typed
# as staff lets every holder bill a label to ANY active customer in the
# whole directory just by typing that customer's F###### code (see
# resolve_billing_customer_id's "otherwise" branch) -- there is no
# secondary check tying a specific staff member to a specific customer.
#
# Deliberately still code-level for this version, same as ASSIGNABLE_ROLE_
# NAMES/PROTECTED_ROLE_NAMES above -- NOT because a DB column here would be
# insecure in principle (an admin already fully controls a role's real
# permissions via the self-service "Manage permissions" UI with zero
# review, so "no code review" was never a strong argument for this
# specific flag either). The actual reasoning: while there is exactly one
# admin-panel operator, the cost of "needs a deploy" is low and any
# mistake would be fully attributed via created_by in the logs regardless.
# If role management ever needs to become a clean, fully self-service SOP
# (multiple admins, frequent new customer-scoped roles), the better fix is
# a real DB column PLUS a deliberate 2-step confirmation on changing it --
# not removing the safeguard, just moving it out of source control. Revisit
# then; don't add the DB column without also adding that confirmation step.
CUSTOMER_IDENTITY_ROLE_NAMES = frozenset({"customer", "fedex_label_agent"})

# Roles whose holders must be assigned specific warehouse(s) --
# warehouse_codes on GroupMember/KefuStaff is required and enforced (via
# core.role_policy.normalize_warehouse_codes) for any role in this set, and
# actively CLEARED for any role not in it. This is a role-level
# classification, same code-owned-allowlist pattern as
# CUSTOMER_IDENTITY_ROLE_NAMES above -- see
# docs/architecture/decisions/adr-010-role-service-policy-declarations.md
# for why this consolidates what was previously three separate
# `role.name == "warehouseman"` branches (api/admin/members.py,
# api/admin/kefu_staff.py, handlers/uchoice/role_change.py) plus a fourth in
# core/pre_confirm_validators.py, and why it's a set of "scoped" role names
# rather than a boolean on service_type: today's warehouse-scope check
# (core.pre_confirm_validators._valid_caller_warehouse_scope) explicitly
# allows genuinely UNSCOPED callers (customer, admin, accountant) to use the
# very same warehouse-touching services without warehouse_codes -- a
# service-level "requires warehouse scope" flag would force every holder of
# that service to have warehouse data, which is not today's behavior. Which
# roles are scoped is a fact about the ROLE, not the service.
#
# "warehouseman" is the original warehouse-scoped role. "warehouse_admin"
# (added 2026-09-15) is a second warehouse-scoped role -- makes uchoice
# inbound/outbound requests and confirms their completion, same warehouse
# assignment requirement as warehouseman, different service grant set
# (see RoleServicePermission, admin-panel self-service per role_registry.py's
# own module docstring on why granting itself stays out of this allowlist).
#
# REQUIRED ORDER before shipping a change that adds a role to this set (or
# that could newly apply to an already-populated role): 1) make the edit
# to this set in your working tree, 2) run
# `python scripts/check_role_policy_impact.py <role_name>` against the
# target database and remediate any reported gap, 3) THEN deploy. Step 2
# must run against a working tree that already has step 1's edit --
# core.role_policy.warehouse_scope_compliance_gap raises ValueError (the
# script exits 1) for a role not yet in this set, rather than silently
# reporting "OK" the way an earlier version of this check did (2026-09-15
# audit, third round). Unlike a RoleServicePermission grant (checked
# automatically by api/admin/roles.py's warehouse_grant_impact), a
# classification change like this has no admin-API call site to hook an
# automatic check onto -- ADR-010 decision 4's impact-check promise,
# satisfied here as a documented manual pre-deploy step rather than an
# enforced one.
WAREHOUSE_SCOPED_ROLE_NAMES = frozenset({"warehouseman", "warehouse_admin"})
