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
