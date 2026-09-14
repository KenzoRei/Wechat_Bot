# ADR-009: Customer-identity roles are a code-level category, not a DB column

**Date:** 2026-09-14
**Status:** Decided

## Context

`fedex_label_agent` was created as a role for an actual customer (yestech)
to make their own FedEx labels directly — narrower than the existing
`customer` role (FedEx only, not the full `uchoice_*` service set), but
functionally the same *kind* of role: it represents one external
customer's own identity, not a staff member acting on behalf of others.

`core/customer_directory.py`'s `resolve_billing_customer_id()` already
distinguished two fundamentally different behaviors:
- **Customer identity**: the caller can only ever bill to their own fixed,
  pre-bound `billing_customer_id`. No message they send can override it.
- **Staff**: the caller specifies *which* customer a label is for, on each
  request — validated only for existence + `status='active'`, not tied to
  any personal binding, because staff legitimately act on behalf of many
  different customers.

Before this change, "is this a customer identity" was hardcoded to the
literal string `"customer"` in five call sites, and `KefuStaff` had no
`billing_customer_id` column at all — `handlers/uchoice/role_change.py`
explicitly refused to ever assign `customer` to a Kefu identity for this
reason. This meant a Kefu-registered customer could place inbound/outbound
requests but could never get a label made through that channel, and there
was no way to model `fedex_label_agent` as customer-identity at all.

## Decision

1. Introduce `core/role_registry.CUSTOMER_IDENTITY_ROLE_NAMES` — a
   code-level allowlist (currently `{"customer", "fedex_label_agent"}`),
   same pattern as the existing `ASSIGNABLE_ROLE_NAMES`/
   `PROTECTED_ROLE_NAMES`. Replace all five `== "customer"` checks with
   membership in this set.
2. Add `billing_customer_id` to `KefuStaff` (`V31`), mirroring
   `GroupMember`'s column exactly (added by `V27`). Remove the hard
   Kefu-can't-be-customer rejection in `role_change.py`/
   `pre_confirm_validators.py`; both channels now support the same binding.
3. Keep the categorization **code-level**, not an admin-panel-editable DB
   column, for this version.

## Alternatives considered

**A DB column on `Role` (e.g. `type IN ('customer', 'staff')`), fully
admin-panel self-service.** Initially rejected by the author on security
grounds: `resolve_billing_customer_id`'s two branches have very different
blast radii if a role is miscategorized — a role mistakenly typed `staff`
lets every holder bill a label to *any* active customer in the whole
directory just by typing that customer's `F######` code, since the staff
path has no check tying a specific staff member to a specific customer.

**This reasoning was directly challenged by the user, correctly, on two
points:**
- The premise that a code-level gate provides real "review" doesn't hold:
  an admin already fully controls a role's actual permissions via the
  self-service "Manage permissions" UI with zero code review (same gap
  already identified for `ASSIGNABLE_ROLES` — see that module's own
  docstring). "No line of code reviewed" was never a strong argument for
  *this* flag either.
- A miscategorization is not a silent/untraceable event — `created_by` in
  the logs fully attributes any action taken under a wrong role type.
  That's a materially different risk than an invisible vulnerability.
- More importantly: hardcoding this one flag breaks the SOP. Role
  creation, permission granting, and deletion are all self-service today;
  making *only* categorization require an engineer to edit source and
  deploy means "role management" isn't a coherent, closable procedure —
  someone could set up a new customer-facing role next month, not know
  `type` needs a separate code change, and hit a silent misconfiguration
  or a blocked customer onboarding with no obvious cause. That failure
  mode is arguably worse than an admin mistake in a dropdown.

## Resolution

Given there is currently exactly one admin-panel operator, the cost of
"needs a deploy" is low relative to the (now understood to be smaller,
attributable) risk, so **code-level for this version** was accepted as the
pragmatic default — not re-affirmed as "more secure," which the
alternatives-considered discussion above already disproves as the
justification.

## Consequences

- Adding a new customer-identity role (a second FedEx-only customer, a
  UPS-only variant, etc.) requires a code change + deploy to
  `CUSTOMER_IDENTITY_ROLE_NAMES`, same friction as `ASSIGNABLE_ROLE_NAMES`
  already has for making any new role assignable at all.
- **Revisit path, if/when role management needs to become a fully
  self-service SOP** (multiple admins, frequent new customer-scoped
  roles): move `type` to a real DB column, but pair it with a deliberate
  2-step confirmation on *changing* that specific field — the
  user's own proposed mitigation, and a better-targeted fix than either
  original position (hardcoded forever, or a bare self-service column).
  Do not add the DB column without also adding that confirmation step.
- `KefuStaff` (table/class name unchanged) is no longer staff-only in
  practice — comments across the codebase (`models/kefu.py`,
  `core/kefu_turn_apply.py`, `core/access_control.py`, etc.) were updated
  to stop asserting "staff are never customers," but the table/class name
  itself was intentionally left as-is rather than renamed mid-adoption.
