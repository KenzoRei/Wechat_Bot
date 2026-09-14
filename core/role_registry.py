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
    AssignableRole("label_agent", "Creates FedEx/UPS shipping labels"),
    AssignableRole("fedex_label_agent", "Creates FedEx shipping labels only"),
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
