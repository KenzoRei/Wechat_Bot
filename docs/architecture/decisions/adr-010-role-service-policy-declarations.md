# ADR-010: Shared policy declarations for role/service attributes, JSONB and EAV deferred

**Date:** 2026-09-15
**Status:** Decided

## Context

Two behavioral attributes exist on role/service assignment today, each grown
independently with its own ad hoc mechanism:

- **Billing customer identity** (`billing_customer_id`, `VARCHAR(7)` FK to
  `customer`, on both `GroupMember` and `KefuStaff`): whether a role's holder
  is bound to one fixed customer account (forced, unoverridable) or may
  select any active customer per request. Classified via
  `core/role_registry.CUSTOMER_IDENTITY_ROLE_NAMES` (see
  [ADR-009](adr-009-customer-identity-roles.md)) and enforced centrally in
  `core/customer_directory.resolve_billing_customer_id()`.
- **Warehouse scope** (`warehouse_codes`, Postgres `ARRAY(String(20))`, same
  two tables): whether a role's holder is restricted to specific
  warehouse(s) for services like `confirm_inbound_completion`,
  `move_storage`, `recount_storage`. Unlike billing identity, there is no
  single code-level classification — "does this role need warehouse codes"
  is re-derived independently by role name in `api/admin/members.py`,
  `core/pre_confirm_validators.py:320`, and
  `handlers/uchoice/role_change.py`, and the actual scope check lives
  separately in `core/pre_confirm_validators.py` (`_valid_caller_warehouse_scope`,
  line 640) plus a defense-in-depth check in `handlers/uchoice/storage_txns.py`.

The user asked for a **generic architecture** to accept future attributes on
roles (e.g. a hypothetical `department`) or services (e.g. a hypothetical
`serv_type`), illustrative examples only — not real requirements — so that
adding the next attribute doesn't require inventing new bespoke plumbing
(new migration + new hardcoded set + new validator + new admin-panel field)
each time.

This decision was reached via structured Claude/Codex collaborative review
(`.collab/tasks/role-permission-attribute-architecture.md`, gitignored,
not part of this repo's history) before being brought to the user.

## Decision

1. **Introduce one shared, typed, code-owned policy declaration** covering
   both existing attributes, replacing `CUSTOMER_IDENTITY_ROLE_NAMES` and the
   three scattered warehouse-by-role-name branches with a single module,
   `core/role_policy.py`. The declaration is a small fixed set of
   *implemented* policies, not a generic key-value bag:
   - Customer-identity classification (role-level): which roles are bound
     to a fixed billing customer vs. select one per request
     (`core.role_registry.CUSTOMER_IDENTITY_ROLE_NAMES`).
   - Warehouse-scope mode (role-level): **explicitly** `scoped` (must have
     `warehouse_codes` assigned; missing data fails closed) or
     `unrestricted` (no warehouse data required) — never inferred from
     `NULL`, since that conflated "forgot to assign" with "role doesn't
     need it." Declared as `core.role_registry.WAREHOUSE_SCOPED_ROLE_NAMES`
     (`warehouseman`, plus `warehouse_admin`, added the same day this ADR
     was written).
   - Each field's policy is a typed `AssignmentFieldPolicy` (field name,
     label, value type, choice source, applicable roles, normalize
     function), collected into `core.role_policy.ASSIGNMENT_FIELD_POLICIES`
     — the next assignment-level field is one more entry here, not a new
     frozenset plus a new pair of ad hoc functions.
2. **One shared assignment-validation function**,
   `core.role_policy.normalize_assignment_fields(db, role_name,
   raw_values_dict)`, used by both the Smart Robot (`GroupMember`) and Kefu
   (`KefuStaff`) admin paths (`api/admin/members.py`,
   `api/admin/kefu_staff.py`) and the conversational role_change path
   (`handlers/uchoice/role_change.py`, `core/pre_confirm_validators.py`) —
   replacing the duplicated per-role-name branches. Raises
   `RoleAssignmentError` tagged with which field failed, so each call site
   can still render its own error convention (English HTTPException detail
   vs. Chinese conversational text) from one shared validation result.
3. **Domain logic stays put and stays domain-specific.** The declaration
   only supplies *classification* that existing functions consult;
   `resolve_billing_customer_id()` keeps its current behavior and call
   sites unchanged — a generic "field must be non-empty" check cannot
   replace it, since billing resolution is asymmetric behavior (forced
   override vs. free selection), not a presence check. The warehouse-scope
   *runtime* check was consolidated (not left as-is) into one function,
   `core.role_policy.check_warehouse_scope(role_name, allowed_codes,
   requested_code)` — see the Corrections section below for why a first
   implementation pass left several call sites still using the old,
   NULL-inferring logic this ADR was meant to replace, and how that was
   found and fixed.
4. **Execution-time enforcement is retained regardless of assignment-time
   validation.** Grants, policies, assignment values, and customer status
   can all change after assignment; pre-confirm/assignment-time validation
   is UX, not the authorization boundary. A policy/grant change that would
   newly violate an existing assignment must be checked for impact before
   taking effect, not silently allowed to leave stale assignments
   non-compliant. Implemented as:
   - `core.role_policy.warehouse_grant_impact(db, role_name, service_name)`,
     called automatically by `api/admin/roles.py`'s
     `grant_role_service_permission` — rejects (409) a `RoleServicePermission`
     grant that would give an already-existing, incompletely-provisioned
     assignment reachability to a warehouse-scoped service. Which services
     count as warehouse-scoped is its own declared set,
     `core.role_policy.WAREHOUSE_SCOPED_SERVICE_NAMES` (the service-
     definition/layer-2 metadata from this ADR's original three-layer
     framing) — every `service_type` name whose validator or handler
     consults `check_warehouse_scope`.
   - `core.role_policy.warehouse_scope_compliance_gap(db, role_name)` +
     `scripts/check_role_policy_impact.py`, a documented **manual**
     pre-deploy gate for the other half of this decision: a code-level
     change to `WAREHOUSE_SCOPED_ROLE_NAMES` itself has no admin-API call
     site to hook an automatic check onto. The function raises `ValueError`
     for a role not (yet) in that set, rather than silently reporting no
     gap — see Corrections below for why that distinction matters and was
     initially wrong.
5. **Expose the same assignment-field descriptors** (requiredness, label,
   value type, choice source) to the admin-panel/API layer — a generic
   `required_fields: list[RequiredFieldDescriptor]` on `GET /admin/roles`
   (`api/schemas.py`, `api/admin/roles.py`), replacing what were briefly two
   separate ad hoc booleans (`customer_identity`, `warehouse_scoped`) during
   an intermediate implementation pass. `api/admin_panel.py` reads this
   list generically; a role newly requiring an existing field needs no
   client-side change. Domain-specific input controls
   (`warehouseChecksHtml`, `billingCustomerInputHtml`) remain explicit,
   per-field adapters, not a generic form renderer.
6. **Typed columns are retained as-is** for both attributes —
   `billing_customer_id` (FK) and `warehouse_codes` (array) stay on
   `GroupMember`/`KefuStaff` exactly as they are. No new tables, no change
   to single-role-per-user assignment, no change to the
   `RoleServicePermission` grant mechanism.

## Corrections found during implementation review

This decision was implemented, then audited by Codex across four rounds
before landing (full record:
`.collab/tasks/role-permission-attribute-architecture.md`, gitignored).
Three rounds found real defects worth recording here, since they shaped
the final shape of decision 3 and decision 4 above:

- **A first implementation pass left the runtime warehouse-scope check
  itself unconsolidated.** `core/pre_confirm_validators.py`,
  `handlers/uchoice/storage_txns.py`, `handlers/uchoice/record_request.py`,
  `handlers/uchoice/lookup_validate.py`, and `handlers/uchoice/address.py`
  each had their own copy of the old `if allowed_codes is None: return
  None` (or equivalent) logic — meaning a warehouse-scoped role
  (`warehouseman`/`warehouse_admin`) with no `warehouse_codes` actually
  assigned was still treated as unrestricted at runtime, even though
  assignment-time validation (decision 1) now correctly required codes for
  *new* assignments. `core.role_policy.check_warehouse_scope` is now the
  single implementation every one of those call sites consults.
- **Rejecting a completion attempt must never mark the original request
  failed.** `handlers/uchoice/storage_txns.py`'s
  `ApplyInboundStorageHandler`/`ApplyOutboundStorageHandler` and
  `handlers/uchoice/lookup_validate.py`'s
  `LookupAndValidateCompletionHandler` operate on a pre-existing TARGET
  request (`targets_existing_request=True` in `service_type`), not a row
  this confirmation session owns. Raising a bare exception there caused
  `core/workflow_engine.py`'s generic exception handler to call
  `mark_failed()` on `session.request_log_id` — the target, not the
  rejected caller's own session — invalidating another person's valid
  request. Fixed by raising `core.workflow_errors.TargetValidationError`
  (never a bare exception) for every rejection in those handlers, matching
  the existing convention already used for their other validation
  failures.
- **The deployment preflight could silently pass for exactly the case it
  exists to catch.** `warehouse_scope_compliance_gap`'s first version
  returned `None` ("no gap") for a role not yet in
  `WAREHOUSE_SCOPED_ROLE_NAMES` — the false-pass this decision's manual
  gate is meant to prevent, since the whole point is checking a role
  *before* it's added to that set. Fixed by raising `ValueError` for an
  undeclared role instead, requiring the documented order (declare in the
  working tree → run the check → deploy) rather than allowing the check to
  be skipped by accident.

Regression coverage for all three:
`tests/uchoice_lifecycle/test_warehouse_scope_fail_closed_regression.py`
and `tests/core/test_role_policy.py`.

## Alternatives considered

**JSONB `attributes` column on `role` and `service_type`, for arbitrary
future metadata.** This was the author's (Claude's) initial proposal:
Postgres-native, GIN-indexable, avoids a migration per new descriptive fact.
Rejected for now — Codex's review noted that with exactly two real
attributes today, both of which drive actual authorization behavior rather
than being purely descriptive, introducing JSONB now would mean shipping
unused machinery (empty columns, an unbuilt schema-driven editor) against a
speculative future need. Descriptive metadata that doesn't drive behavior
(the `department`/`serv_type` examples) is a legitimate future JSONB use
case, but only once a real one is requested.

**EAV-style `attribute_definitions` + `attribute_values` tables**, for
fully self-service, operator-defined attributes. Rejected as disproportionate:
no demonstrated need for operator-defined schema, and a generic value column
does not itself provide per-attribute type or FK integrity — real integrity
still requires the same per-attribute code that typed columns already give
for free.

**A generic rule engine / expression language / ABAC framework**, to let
policies be composed from arbitrary field references. Rejected — the actual
requirement is a small, enumerable set of implemented policies (currently
two), not open-ended rule composition. This would add machinery with no
current consumer and its own correctness surface (injection, evaluation
order, review complexity) disproportionate to the problem.

**A single boolean flag on `service_type`** (e.g. `requires_warehouse_scope`)
to derive warehouse requirements purely from granted services. Rejected —
today's warehouse check explicitly allows *unscoped* callers (customer,
admin, accountant) to invoke the same services without any warehouse data;
a service-level "requires" flag would force warehouse data onto every
holder of that service, changing existing behavior. The scoped/unrestricted
distinction must be declared per role, not inferred purely from which
services a role happens to hold.

## Consequences

- Adding a new attribute that changes authorization behavior (a new kind of
  scope restriction, a new identity-binding rule) still requires code: a new
  entry in the policy declaration plus, if genuinely novel, a new domain
  validator function. This plan removes *duplicated* plumbing across call
  sites, it does not remove the need to implement new behavior in code —
  that boundary is intentional, not an oversight.
- Adding a new *purely descriptive* attribute (no behavioral effect) with no
  storage yet built for it will require a follow-up decision when first
  requested — most likely a JSONB metadata column per this ADR's deferred
  option, revisited at that time rather than pre-built now.
- The warehouse-scope mode declaration is a **new, previously-absent
  behavioral rule** (explicit `scoped`/`unrestricted` per role) — this is a
  deliberate tightening versus today's implicit, NULL-inferred state, and
  must be verified against a role × service × channel matrix before
  rollout: bound-customer override attempts, missing/invalid required data,
  intentionally unrestricted callers, source/destination warehouse checks,
  role-change clearing stale assignment data, and grant/policy changes
  landing between collection and execution. Any intentionally changed
  behavior (e.g. a role that previously worked despite missing warehouse
  data now failing closed) must be documented at implementation time.
- Consistent with ADR-009: identity/policy classification remains
  code-owned (deploy required to add a new policy or reclassify a role),
  not self-service via the admin panel, for this version.
