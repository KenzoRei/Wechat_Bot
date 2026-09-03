# Agreed plan

Status: **APPROVED by the user**, 2026-09-03, after four rounds of
independent technical review (each verified against the source before being
accepted). See `README.md` in this directory for what each round changed.
This is the final, implementation-ready version — the working copy used
during planning lived outside this repository (Claude Code's plan-mode
storage); this file is the durable, checked-in record of what was approved.

Where an in-line note below says "per review" or references "the first
draft," it is describing the review history for context — it does not mean
further changes are pending. This document is the agreed, final state.

---

# Warehouse array, cancel-processing service, and NJ warehouse

## Context

Prior exploration of this WeCom logistics bot (FastAPI + PostgreSQL, U-Choice
warehouse module) surfaced three gaps:

1. A warehouseman/Kefu staff member can only be assigned **one** warehouse.
   The user confirmed a Postgres native array over a join table.
2. Once a customer confirms an inbound/outbound request
   (`request_log.status = 'processing'`), it **cannot be cancelled** — only a
   warehouseman completing it, or (Smart Bot only) a 7-day staleness sweep,
   moves it out of that state. A cancel service is needed, usable by the
   request's own creator and by admins.
3. A third warehouse, **NJ** (120 Raskulinecz Rd, Carteret, NJ 07008), needs
   to become valid and a selectable internal-transfer destination from/to
   JFK and DE.

**This revision incorporates a second review pass** (external audit,
independently verified against the code) that found a critical bug and
several real omissions in the first draft — most importantly, that both
channels' central finalizers unconditionally overwrite a target request's
status right after handlers run, which would have silently clobbered
`cancelled` back to `success` on every single cancellation. That fix, the
Kefu-specific wiring the first draft missed, list-aware warehouse scoping,
and row-level locking against completion/cancellation races are now part of
the plan, not an afterthought.

Resolved product decisions (confirmed with the user):
- **Finalizer fix:** both finalizers check the target's current status before
  overwriting it, rather than introducing a new generic terminal-outcome
  contract.
- **Notification:** if an admin cancels someone else's request, the original
  requester is notified (channel-aware delivery), matching how completion
  already cross-notifies.
- **Migration rollout:** coordinated cutover (migration + deploy back-to-back
  in one short window), not expand/contract — matches this project's
  existing low-volume, manually-applied-migration operating model
  ([docs/operations/migrations.md](../../../operations/migrations.md)).

---

## Part 1 — Warehouse assignment as an array

**Rename, not just retype.** `warehouse_code` (singular) becomes
`warehouse_codes` (plural, `text[]`) on both `group_member` and `kefu_staff`,
matching this codebase's own plural-list convention and disambiguating from
the unrelated, still-singular "which warehouse does this request concern"
`warehouse_code` used elsewhere (session `collected_fields`,
`uchoice_storage`, `uchoice_address`, etc. — **not** touched by this part).

**Rollout:** coordinated cutover. The migration and the code deploy that
reads `warehouse_codes` go out together in one window; no dual-read
compatibility shim. `db/migrations/V22` is written to be applied
immediately before deploying the corresponding app code, consistent with how
this project already applies migrations operationally.

### Migration — `db/migrations/V22__warehouse_assignments_as_array.sql`
```sql
ALTER TABLE group_member
    ALTER COLUMN warehouse_code TYPE character varying(20)[]
    USING CASE WHEN warehouse_code IS NULL THEN NULL ELSE ARRAY[warehouse_code] END;
ALTER TABLE group_member RENAME COLUMN warehouse_code TO warehouse_codes;

ALTER TABLE kefu_staff
    ALTER COLUMN warehouse_code TYPE character varying(20)[]
    USING CASE WHEN warehouse_code IS NULL THEN NULL ELSE ARRAY[warehouse_code] END;
ALTER TABLE kefu_staff RENAME COLUMN warehouse_code TO warehouse_codes;
```
Also updates `role_change`'s `service_type.input_schema` (`warehouse_code` →
`warehouse_codes`, array-of-string) via `jsonb_set`, same technique V9 uses.
Existing single-warehouse assignments become one-element arrays.

**Invariants** (enforced at every write boundary below, not just the DB
type): non-warehouse roles store `NULL`; a warehouseman stores a
**non-empty**, deduplicated, **sorted** list (deterministic order for
rendering/tests); every element is in `VALID_WAREHOUSE_CODES`; an empty
array is invalid input (rejected), never treated as "unrestricted."

### ORM models
- `models/group.py:30` — `GroupMember.warehouse_code` →
  `warehouse_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))`
  (`ARRAY` import precedent: `models/kefu.py:3`).
- `models/kefu.py:42` — same for `KefuStaff`.

### Access control / context plumbing
- `core/access_control.py` — `AccessResult.warehouse_code`
  → `warehouse_codes: list[str] | None`; `check_access` (line 123) and
  `check_kefu_access` (line 214) pass the list field through.
- `core/session_manager.py:144` — `build_context`
  carries `"warehouse_codes"`.
- **List-aware `scope_warehouse` (revised from the original draft, which
  wrongly fell back to `None` — verified that leaves SKU/address candidates
  silently defaulting to JFK (`core/session_manager.py:231`)
  and `pending_request_candidates` returning every warehouse unfiltered
  (`core/uchoice_context.py:161`) for a
  multi-warehouse caller):**
  `core/session_manager.py:216` becomes:
  - If the request already names a `warehouse_code`, that value is used as
    before (and must be one of the caller's assigned codes — enforced at the
    validator/handler boundary, not here).
  - Else, if the caller has exactly one assigned warehouse, use it as the
    default (unchanged from the original draft).
  - Else (zero or multiple assigned), pass the caller's **full assigned set**
    down instead of a single scalar or `None` — `address_candidates` and
    `pending_request_candidates` each gain an
    `allowed_warehouse_codes: list[str] | None` parameter and filter to that
    set (`IN`, not a single `==`) when given; `None` continues to mean "no
    caller-side restriction" only for callers that are genuinely unscoped
    (e.g. a customer role, which was never warehouse-restricted to begin
    with) — never as a stand-in for "this warehouseman has several
    warehouses."
    **`sku_catalog` is a narrower case, per review:** it takes a single
    `warehouse_code: str | None`
    (`core/uchoice_context.py:30-53`) and, when
    given one, adds a per-SKU `in_stock` boolean used as an AI
    disambiguation signal. A multi-warehouse caller with no single resolved
    warehouse has no single stock answer to give — `in_stock` would silently
    mean "in stock somewhere among several warehouses," which is a weaker,
    misleading signal for the exact ambiguity it exists to resolve. Rather
    than inventing a per-warehouse breakdown shape, reuse the function's
    **existing** `warehouse_code=None` behavior (already implemented,
    already omits `in_stock` entirely — see line 45-46) for this case: call
    `sku_catalog(db, warehouse_code=None)` whenever the caller's resolved
    scope isn't a single warehouse, exactly as already happens today for any
    caller with no warehouse context at all. `in_stock` is only ever
    populated once a single warehouse is actually known — unaffected for
    every existing single-warehouse caller.
    The unconditional `"JFK"` business default in
    `core/kefu_turn_apply.py:304` and
    `core/workflow_engine.py:509,530` (customer
    session default when nothing is stated) is unrelated to this and stays
    — that default only ever applies to sessions with no caller-side
    warehouse restriction at all (customers), not to a multi-warehouse
    warehouseman's own scoping.
  - **Actor-aware defaulting at request-creation time, not just candidate
    scoping.** The JFK default isn't only a candidate-list concern —
    `core/workflow_engine.py:494-530`
    (`_resolve_outbound_warehouse_default`/`_resolve_inbound_warehouse_default`)
    and `core/kefu_turn_apply.py:297-307`
    (`_apply_warehouse_default`) unconditionally set `"warehouse_code": "JFK"`
    whenever a `uchoice_inbound_request`/`uchoice_outbound_request` session
    leaves it unstated, with zero awareness of who's asking. Current role
    grants happen to route only the unscoped `customer` role through these
    functions today (V17 removed `uchoice_inbound_request` from
    `warehouseman`), so this isn't reachable in production right now — but
    `group_service_role` is admin-configurable data, not a code invariant,
    and this file is already being touched for the array conversion. Both
    functions gain the same list-aware rule as candidate scoping: unscoped
    caller → JFK default unchanged; caller with exactly one assigned
    warehouse → default to it; caller with several assigned and no
    warehouse stated → ask instead of guessing (never silently assign JFK
    to someone not authorized for it).
  - `pending_request_candidates(db, warehouse_code, service_type_ids)` is
    renamed `pending_request_candidates(db, group_id, allowed_warehouse_codes, service_type_ids)`
    — adds the `group_id` filter it's missing today (flagged in review as a
    pre-existing gap; fixed here since Part 2 needs correct group-scoping
    for its own candidate function and it's the same file/pattern) and
    matches against `allowed_warehouse_codes` with `IN` when given.
- `core/kefu_case_adapter.py:668-671` —
  `_authorize_case`: **`None` must stay unscoped, not collapse to "deny
  everything."** The original scalar code only ran the check at all when
  `access.warehouse_code is not None`; the direct translation is
  ```python
  if access.warehouse_codes is not None:
      if session_warehouse not in access.warehouse_codes:
          return "case_wrong_warehouse"
  ```
  never `session_warehouse not in (access.warehouse_codes or [])` — that
  expression turns admin/accountant's genuinely-unscoped `None` into `[]`,
  which denies every warehouse-specific case for every non-warehouseman
  role. (Caught in review of this plan itself, not just the source code —
  worth calling out since it shows the array translation needs the same
  care at every `is not None` boundary, not just this one.) The same
  `is not None`-gated form applies to
  `handlers/uchoice/lookup_validate.py:61-65`.
- `core/kefu_completion_notice.py:41,64,69`
  — direct `staff.warehouse_code` raw-SQL read, missed in the first draft.
  Becomes `staff.warehouse_codes` with the query changed from
  `rl.result ->> 'warehouse_code' = :wh` to
  `rl.result ->> 'warehouse_code' = ANY(:whs)`.
- `handlers/uchoice/lookup_validate.py:61-65`
  — containment check (`caller_warehouses`, `not in`). **Also** gains the
  `populate_existing().with_for_update()` locked, refreshed fetch on the
  initial target read — see Part 2 Fix 3; this file is shared
  infrastructure for completion, and the same lock is required here to
  serialize against a concurrent cancellation attempt on the same row.

### Enforcing assigned warehouses on ordinary services (blocker, per review)
Everything above wires `warehouse_codes` through to `context`, and enforces
it for `_authorize_case` and the completion lookup handlers — but that's not
the same as enforcing it everywhere a warehouseman names or omits a target
warehouse. Confirmed against the code: **no existing pre-confirm validator
in `core/pre_confirm_validators.py` compares
`collected_fields["warehouse_code"]` against the caller's own assignment**
for `adjust_storage`, `move_storage`, `recount_storage`,
`uchoice_inbound_request`/`uchoice_outbound_request`, `view_storage_history`,
`view_invoice`, or `upsert_address` — a warehouseman assigned only
`["JFK","NJ"]` could today explicitly submit a DE adjustment and reach the
mutation. Separately, `handlers/uchoice/queries.py:1-34`'s
`QueryStorageHandler` (`view_storage`) is confirmed "deliberately
zero-argument: always shows every warehouse's full inventory" — it doesn't
even read `collected_fields` today, so no per-caller candidate scoping
upstream of it can restrict what it returns. Both are pre-existing gaps
(true even for the current single-`warehouse_code` design — a warehouseman
assigned only `'JFK'` could already explicitly name `'DE'` today), not
something this array conversion introduces, but this plan already claims
(above) that an explicitly-named warehouse "must be one of the caller's
assigned codes... enforced at the validator/handler boundary" — so that
claim needs to actually be delivered somewhere, not left as a forward
reference with no destination.

**Shared rule**, added to `core/pre_confirm_validators.py`:
```python
def _valid_caller_warehouse_scope(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    allowed = context.get("warehouse_codes")
    if allowed is None:
        return None  # unscoped caller (customer/admin/accountant) — no restriction
    requested = collected_fields.get("warehouse_code")
    if requested and requested not in allowed:
        return "该仓库不在您的权限范围内。"
    return None
```
Composed (via the file's existing `_compose()` helper) into the
`PRE_CONFIRM_VALIDATORS` entries for `uchoice_inbound_request`,
`uchoice_outbound_request`, `adjust_storage`, `move_storage`,
`recount_storage`, `view_storage_history`, `view_invoice`, and
`upsert_address` — every service whose `collected_fields` carries a
`warehouse_code` naming which warehouse the operation targets. **Not**
composed into the two cancel-service validators — cancellation is
deliberately scoped by ownership/admin plus `group_id`, not by warehouse,
per Part 2's design; a warehouseman isn't even among the roles granted
either cancel service.

**Execution-time backstop**, matching this codebase's existing "validate at
both the pre-confirm and execution boundaries" pattern (already used for
`role_change`, and now for completion/cancellation via Fix 3's lock): the
storage-mutating handlers in
`handlers/uchoice/storage_txns.py`
(`AdjustStorageHandler`, `MoveStorageHandler`, `RecountStorageHandler`) and
`handlers/uchoice/record_request.py`
(the inbound/outbound creation handler) each re-run the same
`context.get("warehouse_codes")` containment check immediately before
mutating, raising `RuntimeError` on violation — closing the gap between a
pre-confirm check and a later confirm-turn actually executing it, the same
reasoning already documented for why `role_change`'s handler re-checks what
its pre-confirm validator already checked.

**`view_storage` (no pre-confirm step — `requires_confirmation=false`,
executes immediately):** `QueryStorageHandler.handle()` starts reading
`context`/`collected_fields` for the first time — filters the query to
`UchoiceStorage.warehouse_code == collected_fields["warehouse_code"]` when
an explicit code is given (validated against `context.get("warehouse_codes")`
the same way, when the caller is restricted), else, for a warehouse-
restricted caller, `UchoiceStorage.warehouse_code.in_(context["warehouse_codes"])`;
an unscoped caller (`warehouse_codes is None`) continues to see every
warehouse, unchanged. This also happens to make `view_storage`'s
already-declared-but-currently-ignored optional `warehouse_code`/`sku_code`
input-schema fields actually functional, which they are not today.

### Assigning multiple warehouses (role_change + admin API)
- `handlers/uchoice/role_change.py:27,45,78`
  — reads `fields.get("warehouse_codes")` (list), validates every element
  against `VALID_WAREHOUSE_CODES`, rejects an empty list, stores
  `sorted(set(codes))` or `None` for non-warehouseman.
- `core/pre_confirm_validators.py:287-324`
  — `_valid_role_change_target_and_role` gets the equivalent list-based
  check (non-empty, every element valid).
- **New, per review:** `core/uchoice_field_sanitization.py:91`
  (`_sanitize_role_change_fields_before_persistence`) — this file's own
  stated purpose is to stop invalid AI-extracted values from ever reaching
  persisted `collected_fields` (it already does this for `target_openid`
  and `new_role`; `warehouse_code`/`warehouse_codes` was never covered even
  in the current singular form). Add the same treatment: **first check the
  top-level value is actually a `list`** (mirroring
  `sanitize_extracted_fields_before_persistence`'s own existing
  `isinstance(lines, list)` guard for `sku_lines` a few lines above — a
  malformed non-list AI-extracted value, e.g. a bare string, must not be
  iterated element-by-element as if it already were a list of codes, since
  Python happily iterates a string's *characters*), dropping the whole
  field if it isn't one; then drop any individual element not in
  `VALID_WAREHOUSE_CODES` rather than persisting a fabricated code —
  consistent with this file's existing "preserve valid progress, drop only
  the bad part" pattern at both levels.
- `core/confirmation.py:94,484-496` —
  `_role_change_sections_builder` (pre-confirm display) shows a
  comma-joined list; add a `"warehouse_codes"` `_FIELD_LABELS` entry.
- **New, per review:** `core/result_message.py:420-432`
  — `_role_change_result_sections_builder`, a **second**, separate
  post-execution result-display builder for `role_change` that also reads
  `fields.get("warehouse_code")` and was missed in the first draft. Same
  comma-joined-list treatment.
- `core/kefu_turn_apply.py:35` — `_FIELD_PROMPTS`
  key/prompt updated to ask for one or more codes. **Built from
  `sorted(VALID_WAREHOUSE_CODES)` at call time, not a hardcoded string** —
  the first draft's prompt text hardcoded "JFK、DE 或 NJ", which wrongly
  coupled Part 1 to Part 3 and would need editing again for a future fourth
  warehouse. Same fix applies to the `core/pre_confirm_validators.py:322`
  error message (Part 3 section below).

### Admin REST API + panel
- `api/schemas.py` — `MemberCreate`, `MemberUpdate`,
  `MemberResponse`, `KefuStaffUpdate`, `KefuStaffResponse`:
  `warehouse_code: str | None` → `warehouse_codes: list[str] | None`.
- `api/admin/members.py` /
  `api/admin/kefu_staff.py` — `_clean_warehouse_code`
  → `_clean_warehouse_codes(raw: list[str] | None) -> list[str] | None`:
  strips/dedupes/sorts, 400s on an unknown code or an empty resulting list
  for role=warehouseman, applied identically in both files' add/update
  routes.
- `api/admin_panel.py:291-319,435` — warehouse
  `<input>` becomes comma-separated, parsed client-side into a list sent as
  `body.warehouse_codes`; table renders `(m.warehouse_codes || []).join(", ")`.

### Docs
- `docs/operations/admin-api.md:149,353` and
  `api/admin/invoices.py:27` — update
  singular/hardcoded "JFK or DE" wording (folded in here since it's the same
  edit class as Part 3's doc fixes).

### Tests
Representative call sites needing `warehouse_codes=[...]` instead of
`warehouse_code=...`:
`tests/uchoice_self_registration/test_role_change_kefu_identity.py:145`,
`tests/uchoice_self_registration/test_kefu_case_session_resolution.py:128`,
`tests/kefu_integration/test_kefu_staff_admin_api.py`.
New coverage: `_authorize_case` allows a JFK case and a NJ case but denies a
DE case for `warehouse_codes=["JFK","NJ"]`; admin-API assignment of two
codes; `_sanitize_role_change_fields_before_persistence` drops an unknown
code from a list without dropping the rest of the turn's fields;
`kefu_completion_notice` matches a completion whose `warehouse_code` is any
one of the staff member's assigned codes; multi-warehouse **address** and
**pending-request** candidate queries exclude a warehouse not in the
caller's set when the caller has more than one assigned; **`sku_catalog`
for a multi-warehouse, unresolved caller returns the full catalog with no
`in_stock` field at all** (not a filtered-but-populated one — corrected
from an earlier draft of this test description that assumed the same
exclusion shape as the address/pending-request cases); single-warehouse
default behavior unchanged for all three. **New, for the warehouse-scope
enforcement fix:** a warehouseman assigned `["JFK","NJ"]` is rejected at
pre-confirm (and, driving straight to execution without going through
confirm, at the handler) for an explicit `adjust_storage`/`move_storage`/
`recount_storage`/`uchoice_inbound_request`/`uchoice_outbound_request`
targeting `"DE"`; same rejection for `view_storage_history`/`view_invoice`
explicitly requesting `"DE"`; `view_storage` with no explicit warehouse
returns only `JFK`+`NJ` rows for that same caller, and every warehouse for
an unscoped (customer/admin/accountant) caller; an admin or accountant
(`warehouse_codes is None`) is never blocked by any of these checks
regardless of which warehouse they name.

---

## Part 2 — Cancel a confirmed-but-not-yet-completed request

Two new services, `cancel_inbound_request` / `cancel_outbound_request`,
mirroring `confirm_inbound_completion` / `confirm_outbound_completion`'s
`targets_existing_request` mechanism and per-direction split — but
transitioning the target to `status='cancelled'` instead of running storage
handlers.

**Authorization:** the request's original creator (Smart Bot:
`request_log.wechat_openid` match **and** `source_channel == 'smart_robot'`;
Kefu: `request_log.submitted_by_staff_id` match **and**
`source_channel == 'kefu'` — fail-closed on any other/inconsistent
provenance) or an admin. **Tenant scope:** always additionally requires
`target.group_id == access.group_id` — an admin cancels within their own
group only, never system-wide. **State scope:** only `status == 'processing'`
requests.

**A second review round found that the round-1 concurrency fix (row lock
inside the lookup handlers) was necessary but not sufficient** — both
pipelines mutate the target's status *before* any lookup handler runs at
all, and the exception path for a losing race would have overwritten the
winner's terminal status with `failed`. Both are fixed below (Fix 1),
ahead of the locking fix (Fix 3) that depends on them.

### Fix 1 (blocker) — never touch the target's status before the locked lookup
For a `targets_existing_request` session, `session.request_log_id` points at
the pre-existing **target** row, not a row this session owns. Both
pipelines currently force that row to `'processing'` unconditionally, before
any status check or lock:
- `core/workflow_engine.py:891-915` —
  `_execute_workflow_and_finish`'s docstring states as its first documented
  action: "Transitions the log to 'processing'"; line 914-915 calls
  `request_logger.mark_processing(db, session.request_log_id)`
  unconditionally, before the DB-phase/side-effect split even begins.
- `core/kefu_turn_apply.py:598` —
  `confirm_kefu_turn` sets `log.status = "processing"` inline, before
  `_finish_execution` runs any workflow step.

Concretely, this reorders a race into a real bug even with Fix 3's lock in
place: a request already `success` (completed by someone else) gets forced
back to `processing` by a *cancellation* confirm turn, before that turn's
own locked lookup ever runs — so the lock then correctly observes
`processing` and wrongly cancels an already-completed request. The
clobbering write happens before the lock, not after it.

**Fix:** both pipelines skip this pre-transition for
`targets_existing_request` services. Only a session's *own* freshly-created
log (an ordinary `pending → processing` transition for a request this
session actually owns) transitions here; a `targets_existing_request`
session's target is left completely untouched until its locked lookup
handler runs. That lookup handler (Fix 3)
becomes the first and only authoritative place that evaluates and (for
cancellation) transitions the target's state — `LookupAndValidateCompletionHandler`
already re-checks `status == 'processing'` as a redundant safety check
today; after this fix, that check becomes the *primary* one, holding a real
lock, not a late-stage sanity re-check.

### Fix 2 (blocker) — a losing race must not mark the target `failed`
Once Fix 1 is in place, a losing attempt's locked lookup handler discovers
the target is no longer `'processing'` and must fail out *without touching
the target at all* — but the existing exception handling paths call
`mark_failed`/set `status='failed'` **unconditionally** on
`session.request_log_id`, which is still the target:
- `core/workflow_engine.py:959-966` (single-phase)
  and `core/workflow_engine.py:985-993` (DB-phase
  split) both call `request_logger.mark_failed(db, session.request_log_id, ...)`
  on any exception, including this one.
- Kefu's equivalent exception handling around
  `core/kefu_case_adapter.py:557-559`
  (`kefu_turn_apply.confirm_kefu_turn(...)`) needs the same treatment.

**Fix (typed business conflict, not an operational failure) — and, per
review, channel-specific handling of *how* it's caught:** the locked lookup
handlers (`LookupAndValidateCompletionHandler` and the new
`LookupAndValidateCancellationHandler`) raise a distinct
`TargetAlreadyResolvedError(current_status, serial_number)` — defined in a
new, neutral `core/workflow_errors.py` (not `core/workflow_engine.py`,
which `handlers/uchoice/*.py` cannot import from without a cycle, since
`workflow_engine.py` imports `HANDLER_REGISTRY` which imports those same
handler modules) — instead of a bare `RuntimeError` when the locked read
shows a non-`'processing'` status. **The two channels must not treat this
exception the same way:**

- **Smart Bot:** `_execute_workflow_and_finish`'s exception handling (both
  phases) catches `TargetAlreadyResolvedError` before the generic handler:
  rolls back (safe here — a Smart Bot confirm turn carries no
  cross-turn replay ledger, unlike Kefu), closes only the *attempting*
  session as `status="cancelled"` (reusing the exact vocabulary
  `core/workflow_engine.py`'s `_resolve_target_request`
  already uses for the equivalent unlocked pre-check), and replies with the
  target's actual current state. Never calls `mark_failed`/`mark_success`
  on the target — untouched, exactly as the winner left it.
- **Kefu — a plain `db.rollback()` here is actively wrong, not just
  inelegant, and this is a design correction from the previous round, not
  a refinement of it:** Kefu's outer transaction (owned by the big
  turn-processing function in
  `core/kefu_case_adapter.py`) also holds the
  `CaseExecution` claim row acquired earlier via `_acquire_execution`
  (`core/kefu_case_adapter.py:196-230`) and
  the rest of this turn's replay/idempotency bookkeeping — all uncommitted
  until the single outer `db.commit()` near line 644. Rolling back here
  would discard that claim along with everything else, breaking the
  duplicate-message replay guarantee this exact machinery exists to
  provide (a retried/duplicate Kefu message for this same `msgid` would no
  longer find its `case_turn` row and would re-run from scratch instead of
  replaying). **Fix:** Kefu never lets this exception cross the
  orchestration boundary at all. `confirm_kefu_turn`
  (`core/kefu_turn_apply.py:586-601`) catches
  `TargetAlreadyResolvedError` locally, right around its call into
  `_finish_execution`/`_workflow_steps` — the same place, and the same
  shape, as the *existing*
  `if session.status != "pending_confirmation": reply = "该申请已处理或已关闭，不能重复确认。"`
  early-return two lines above it. On catch: set `session.status = "cancelled"`,
  build a reply via `render_kefu_outcome` describing the target's actual
  current state (a new `TargetAlreadyResolvedOutcome`, same family as the
  existing `ConfirmationAlreadyProcessedOutcome`), and return that string
  **normally** — no rollback, nothing thrown further. Control returns to
  `core/kefu_case_adapter.py`'s turn function exactly as if this were any
  other outcome; `_finalize_turn` runs as normal in the same transaction,
  correctly recording the `case_turn`/execution-ledger state for this
  attempt (a real turn that happened and got a real, final answer — "already
  processed" — not a crash), and the single outer `db.commit()` persists
  all of it together. The target itself was never touched by any of this.

### Fix 3 (blocker) — row-level locking, force-refreshed
No existing lock prevents a completion attempt and a cancellation attempt
from both observing `status='processing'` on the same row and both
proceeding. Kefu's advisory lock (`core/kefu_case_adapter.py:198`)
is scoped per-message (`execution_key`) for retry idempotency only — it does
nothing to serialize two *different* actors' attempts.

**Fix:** both `LookupAndValidateCompletionHandler`
(`handlers/uchoice/lookup_validate.py:40`)
and the new `LookupAndValidateCancellationHandler` fetch the target with
```python
target = (
    db.query(RequestLog)
    .filter_by(log_id=request_log_id)
    .populate_existing()
    .with_for_update()
    .first()
)
```
**`populate_existing()` is required, not optional** — the same `db` Session
already loaded this exact row earlier in the same turn (e.g.
`_resolve_target_request`'s unlocked pre-check runs during
`_on_all_fields_collected`, well before confirmation). Without
`populate_existing()`, SQLAlchemy's identity map returns the *already-cached
Python object* without refreshing its attributes from the now-locked read —
the SQL-level lock is real and correctly serializes the transactions, but
the losing transaction would still see the stale in-memory `status` it
loaded before blocking, defeating the lock's entire purpose. This is
exactly the scenario the concurrency test (below) needs to construct
deliberately (load the target once via a normal query first, *then* start
the two competing locked attempts), not just "run two attempts" — a naive
test could pass by accident even with the identity-map bug present.

Verified safe with the existing DB-phase/side-effect-phase transaction
split: the lock is held only for the DB-phase transaction, released on that
transaction's commit — by which point the row's terminal status is already
durable, so the losing concurrent transaction's locked, refreshed read
correctly observes it.

### Fix 4 (blocker) — Kefu-specific wiring, entirely missed in the first draft
The first draft only touched `core/workflow_engine.py`'s
`_REFERENCE_SERIAL_CANDIDATE_KEYS`, which does not drive Kefu at all. Kefu
runs its own separate pipeline (`core/kefu_turn_apply.py`,
`core/kefu_case_adapter.py`) with independent
gates:
- `core/kefu_case_adapter.py:99-112` —
  `_KEFU_ENABLED_SERVICES` must include both new service names, or Kefu
  rejects them before execution.
- `core/kefu_turn_apply.py:84-87` —
  `_PENDING_CANDIDATE_KEYS` gets
  `"cancel_inbound_request": ("cancelable_inbound_requests", "入库申请")` /
  `"cancel_outbound_request": ("cancelable_outbound_requests", "出库申请")`.
- `core/kefu_turn_apply.py:47` —
  `_SERVICE_LABELS` gets Chinese labels for deterministic Kefu wording.
- `ai/prompt_builder.py:137-156` — the
  candidate-disambiguation instructions currently name
  `pending_inbound_requests`/`pending_outbound_requests` explicitly; extend
  this block (or generalize it to read the candidate key names from a
  shared list instead of hardcoding two) so the AI applies the same
  0/1/N disambiguation rules to the new candidate keys.

### Transaction split — cancellation needs it too
Cancellation needs `_UCHOICE_SPLIT_ELIGIBLE_SERVICES` treatment even though
it doesn't mutate storage: the new cross-notify step (Fix 5 below) is an
external call, and running it in the same single-phase transaction as the
status mutation means a notification failure would trigger `db.rollback()`
+ (post-Fix-2) a guarded `mark_failed` — which would still wrongly revert a
cancellation that already logically succeeded, just via a cleaner failure
mode than before. Exactly the reason `_SIDE_EFFECT_STEP_TYPES` exists for
`complete_existing_request`/`reply_wechat` today.
`core/workflow_engine.py:939-947`
(`_UCHOICE_SPLIT_ELIGIBLE_SERVICES`) gains `"cancel_inbound_request"`,
`"cancel_outbound_request"`.
`core/workflow_engine.py:1063-1067`
(`_SIDE_EFFECT_STEP_TYPES`) gains the new `notify_cancelled_request` step
type. `cancel_existing_request` (the DB mutation) stays in the DB phase,
alongside `lookup_and_validate_cancellation`.

### Fix 5 (blocker) — channel-aware notification, explicitly best-effort
Per the confirmed decision: notify the original requester when an admin
cancels someone else's request (skip notifying if the canceller *is* the
original requester — redundant with their own direct reply).

**Delivery guarantee, stated explicitly (per review's ask to pick one, not
leave it implied): best-effort, not durable.** This matches the existing,
already-shipped precedent for the exact same class of notification —
`handlers/uchoice/complete_request.py:34-40`'s
cross-group push for `confirm_inbound_completion`/`confirm_outbound_completion`
today catches `RuntimeError`, logs it, and explicitly does *not* fail the
already-committed completion. A real, pre-existing, already-accepted risk
window comes with that choice (process dies between the DB-phase commit and
the side-effect phase actually running, on either channel) — not a new risk
this feature introduces, and not worth solving here with a new durable
outbox subsystem when the codebase's one existing analogous feature doesn't
have one either. `NotifyCancelledRequestHandler` follows the identical
shape: any exception it raises is caught and logged, never re-raised into
the surrounding pipeline, so a notification failure can never mark the
already-committed cancellation as failed on either channel.

**Concrete dispatch, named per channel (the previous draft described the
*behavior* without naming what code performs it):**
- **Smart Bot:** `NotifyCancelledRequestHandler.handle()` calls
  `send_group_webhook_message` **directly, inline** — no deferral needed.
  This is safe *because* `notify_cancelled_request` is in
  `_SIDE_EFFECT_STEP_TYPES` (Transaction split, above), which
  `core/workflow_engine.py`'s `_run_workflow_steps`
  already guarantees only runs after the DB-phase transaction (the actual
  cancellation) has committed.
- **Kefu:** `_SIDE_EFFECT_STEP_TYPES` is read only by
  `core/workflow_engine.py`'s split — Kefu's own `_workflow_steps()`
  (`core/kefu_turn_apply.py:472`) ignores it
  and runs every handler inside Kefu's one transaction, which commits once,
  later, inside `core/kefu_case_adapter.py`
  (after `_finalize_turn`, around line 644). So on this channel,
  `NotifyCancelledRequestHandler.handle()` branches on
  `context.get("source_channel")` — when it's `"kefu"`, it does **not**
  call `send_group_webhook_message` inline; it appends
  `{"webhook_url": ..., "content": ...}` to
  `context.setdefault("_deferred_webhook_notifications", [])` instead.
  **Named dispatcher:** the same turn-orchestration function in
  `core/kefu_case_adapter.py` that already runs the code around line
  629-644 (marking `execution_row`/`confirmation_execution_row` completed
  right before the outer `db.commit()`) gains one more step, **after** that
  `db.commit()` succeeds: iterate
  `context.get("_deferred_webhook_notifications", [])` and call
  `send_group_webhook_message` for each, wrapped in the same catch-and-log
  (never re-raise) as the Smart Bot case.
- **Kefu-originated target, notify a Kefu staff member (either channel
  cancelling):** no deferral needed at all — `core.kefu_delivery.enqueue_text`
  is just a durable row insert, delivered later by the existing separate
  poller, not a synchronous external call. On the Kefu-cancelling path it's
  written in the *same* transaction as everything else Kefu writes for this
  turn (case_turn, execution ledger, etc.) and commits with it. On the
  Smart-Bot-cancelling path (notifying a Kefu-originated request's staff
  submitter), it runs inside `notify_cancelled_request`'s own post-commit
  side-effect phase — a **separate**, later transaction from the DB-phase
  one that already committed the cancellation itself (per the Transaction
  split above), not literally "the same transaction as everything else" —
  but still just a plain row insert, so it inherits the same best-effort
  guarantee as the phase's `db.commit()` at the end of that second
  transaction: durable once that commit succeeds, consistent with the
  best-effort delivery guarantee already stated above. Either way, this is
  already the same durable-outbox shape
  `core/kefu_completion_notice.py`/`core/kefu_delivery.py` use elsewhere.

Idempotency key for the `enqueue_text` case:
`f"request-cancelled:{target.log_id}:{recipient_staff_id}"` — deterministic,
safe against a retried Kefu turn re-running this step.

### New handlers — `handlers/uchoice/cancel_request.py`
- `LookupAndValidateCancellationHandler` — locked, refreshed fetch (Fix 3),
  raises `TargetAlreadyResolvedError` (Fix 2) if the locked read shows
  anything but `'processing'`; otherwise checks direction match (same
  mismatch guard as completion), `target.group_id == context["group_id"]`,
  and the admin-or-fail-closed-owner check above; stashes
  `context["_uchoice_target"]`.
- `CancelExistingRequestHandler` — sets `target.status = "cancelled"` and
  `completed_at` **only** (explicitly does *not* touch `target.result` —
  see the result-preservation note under Fix 1/2's Smart Bot path and the
  Kefu note below; no external call either, that's the separated
  `notify_cancelled_request` step now).
Both registered in `handlers/registry.py` under
`lookup_and_validate_cancellation` / `cancel_existing_request` /
`notify_cancelled_request`. `TargetAlreadyResolvedError` itself lives in the
new `core/workflow_errors.py` (Fix 2) — not in either handler module or in
`core/workflow_engine.py` — since it's raised by handlers and caught by two
separate orchestration modules (`core/workflow_engine.py` and
`core/kefu_turn_apply.py`), and none of those three should import it from
one of the others.

**`request_log.result` preservation, explicit fix:**
`core/kefu_turn_apply.py:572-578`'s
`log.result = context.get("result", {})` currently sits **outside** the
status branch — it would overwrite the original request's stored result
(e.g. a completed request's storage/PDF details) with whatever
`CancelExistingRequestHandler` happens to return, even after Fix 1/2 guard
the status assignment correctly. The guard from Fix 1 must wrap *both*
lines together:
```python
if log.status == "processing":
    if not service.get("awaits_completion", False):
        log.status = "success"
        log.completed_at = now
    log.result = context.get("result", {})
```
**`completed_at` stays inside the `not awaits_completion` branch, not
hoisted above it** — an earlier draft of this fix incorrectly stamped
`completed_at` unconditionally, which would mark a brand-new
`uchoice_inbound_request`/`uchoice_outbound_request` submission (status
correctly stays `'processing'`, awaiting a warehouseman) as if it had a
completion timestamp while its status says otherwise. This exactly mirrors
the original unguarded code's own branch structure
(`core/kefu_turn_apply.py:572-578`) — the fix
adds the `if log.status == "processing":` wrapper around it, nothing else
changes shape. Smart Bot's side is already correct once
`request_logger.mark_success` is guarded as a whole function (Fix 1) —
`result` and `completed_at` are both inside that function's existing body,
so skipping the function skips both together, no separate fix needed
there.

### Pre-confirm validator
`core/pre_confirm_validators.py` — new
`_valid_cancel_target_and_owner`, registered for both new service names.
Same status/direction/group/ownership checks as the handler (defense in
depth, matching this codebase's existing pattern of validating at both
boundaries) — **without** the row lock, which only makes sense at the
actual execution boundary.

### Candidate list — ownership-and-group scoped, not warehouse scoped
`core/uchoice_context.py` — new
`cancelable_request_candidates(db, group_id, service_type_ids, access)`:
`status='processing'`, `service_type_id IN (...)`, **`group_id` match**
(tenant isolation — admins see every eligible row in their own group, never
system-wide), and ownership filter unless `access.role == "admin"`
(channel-aware: `wechat_openid` match for Smart Bot, `staff_id` match for
Kefu).
`core/session_manager.py:289-297` — wired in
next to the existing completion-candidate blocks, injecting
`"cancelable_inbound_requests"` / `"cancelable_outbound_requests"`.
`core/workflow_engine.py:304-307` —
`_REFERENCE_SERIAL_CANDIDATE_KEYS` gets the Smart-Bot-side entries (Fix 4
above adds the Kefu-side equivalent).

### Confirmation and result rendering
`core/confirmation.py` — `_DISPLAY_NAMES` entries
("取消入库申请" / "取消出库申请"), a shared `_cancel_request_sections_builder`
(target serial + original SKU/destination summary via
`resolve_completion_target`) registered in `CONFIRMATION_BUILDERS`.

`core/result_message.py:69-88` — the generic
fallback title (`f"{display}已完成"`) would render a cancellation as "取消
入库申请已完成", which reads as "the cancellation completed" rather than "the
request was cancelled" — confusing next to every other `已完成` title in
this list, which all mean the opposite (the underlying operation
succeeded). Add explicit `_RESULT_TITLE_BUILDERS` entries
(`"入库申请已取消"` / `"出库申请已取消"`), same shape as the existing
`_awaits_completion_result_title`/`_inbound_completion_result_title`
entries — no sections needed beyond the title, matching that precedent's
"details already shown at confirm time, stay short" reasoning.

### Migration — `db/migrations/V23__cancel_pending_uchoice_requests.sql`
Name-joined against the existing group, following
V15's exact idiom —
**`ON CONFLICT ... DO UPDATE` for definitional rows** (`service_type`,
`workflow`, `group_service`), so a corrected re-run of this migration
number during development can't silently leave a half-configured catalog
entry in place; **`ON CONFLICT DO NOTHING` for `group_service_role`**
specifically, so re-running the migration never silently re-adds a grant an
admin deliberately revoked afterward (V15 makes exactly this distinction,
not a blanket choice either way):
- Two `service_type` rows, `targets_existing_request=true`,
  `requires_confirmation=true`, `awaits_completion=false`, keywords like
  `["取消入库申请","作废入库"]` / `["取消出库申请","作废出库"]` (distinct from
  the generic mid-session "取消" intent).
- Two `workflow`s (deterministic, hardcoded `workflow_id`s — matching the
  existing seed convention of fixed UUIDs for referenceable rows, e.g.
  V2's confirm_inbound_completion),
  4 `workflow_step`s each: `lookup_and_validate_cancellation` →
  `cancel_existing_request` → `notify_cancelled_request` → `reply_wechat`.
  **`workflow_step` has no unique constraint on `(workflow_id, step_order)`**
  (confirmed against `V1__initial_schema.sql:320-329` —
  only `step_id` is a primary key), so a plain `INSERT` is not safely
  re-runnable the way the `ON CONFLICT`-based rows above are. Since these
  are brand-new workflows (not a reshuffle of an existing one, unlike
  V6's precedent), the
  migration deletes-then-inserts deterministically for each workflow:
  `DELETE FROM workflow_step WHERE workflow_id = <fixed uuid>;` immediately
  followed by the 4 `INSERT`s — idempotent by construction, safe against a
  partial or repeated re-run before this migration number is marked applied.
- `group_service` rows linking both to the production group.
- `group_service_role` grants: `customer` and `admin` only (per confirmed
  scope — warehousemen excluded).

### Tests
`tests/uchoice_lifecycle/test_cancel_processing_request.py` plus a
PostgreSQL-marked concurrency test:
- Full Smart Bot cancellation flow ending `status == 'cancelled'`; full Kefu
  cancellation flow, same assertion.
- Confirmation finalizers do not overwrite a cancellation back to `success`
  (regression test for Fix 1's guard).
- Original `request_log.result` is preserved through both a completion and
  a cancellation attempt (regression test for the explicit result-guard fix
  above) — assert the *pre-existing* result payload survives, not just that
  the field is non-null.
- Owner can cancel their own request (both channels); admin can cancel
  anyone's *within their group*; a different customer is denied; a
  cross-group reference is denied even for an admin of a different group;
  Smart Bot and Kefu ownership fields are never cross-matched (a Kefu
  `submitted_by_staff_id` never satisfies a Smart Bot `wechat_openid` check
  or vice versa).
- A `pending` (not yet confirmed) request is rejected as not in a
  cancellable state.
- **A losing race gets an "already resolved" outcome, and the target is
  left completely untouched** (regression test for Fix 1+2 together): drive
  a completion to success first, then attempt a cancellation confirm turn
  against the same now-completed target — assert the target's `status` and
  `result` are both unchanged, and that the *attempting* cancellation
  session closes as `cancelled`/"already processed" rather than the target
  ending up `failed`.
- **Concurrency (Postgres-marked), constructed to actually exercise the
  identity-map fix — not just "run two transactions":** load the target row
  once via a plain (unlocked) query in a setup step first, so it's already
  in each test session's identity map, *then* start one transaction running
  completion and another running cancellation against that same row.
  Exactly one of `processing → cancelled` or `processing → success` (+
  inventory mutation) wins; never both; never a cancelled row with an
  applied inventory delta; the loser's locked read must reflect the
  winner's committed status, not a stale cached value.
- Admin-cancels-someone-else's-request triggers the correct channel-aware
  notification to the original requester; self-cancellation does not
  double-notify; a Kefu-side admin cancelling a Smart-Bot-originated
  request only sends the webhook notification after the Kefu transaction's
  own commit succeeds (regression test for Fix 5 — e.g. force a failure
  immediately after the deferred-notification list is populated but before
  Kefu's outer commit, and assert no webhook call was made).

---

## Part 3 — Add NJ warehouse

(Largely unchanged from the first draft — reviewed as structurally sound.
Doc/string fixes expanded per review.)

### Constant
`core/uchoice_constants.py:14` —
`VALID_WAREHOUSE_CODES = frozenset({"JFK", "DE", "NJ"})`. Propagates
automatically to `jobs/uchoice_daily.py`, `handlers/uchoice/role_change.py`,
`core/pre_confirm_validators.py`, and (per Part 1's fix) the now-dynamic
prompt/error text.

### Migration — `db/migrations/V24__add_nj_warehouse.sql`
Written with explicit column lists (not positional `VALUES (...)`, per
review, since a positional insert silently breaks if the table's column
order ever changes) and stable, deterministic `address_id`s so the
migration is safely re-runnable / has a real `ON CONFLICT` target.
`created_by` follows the seed convention (`'system'` / `'migration_v24'`);
`customer_id` is left `NULL` for these warehouse-owned rows, matching every
existing warehouse-transfer/self-pickup address (they're operational
addresses, not tied to a customer directory entry).

1. **Internal-transfer address book**, mirroring
   `V2__seed_catalog.sql:76-78`:

   | company_name | charge_type | addr | warehouse_code (source scope) | destination_warehouse_code |
   |---|---|---|---|---|
   | NJ Warehouse | truck_transfer | 120 Raskulinecz Rd, Carteret, NJ 07008 | JFK | NJ |
   | NJ Warehouse | truck_transfer | 120 Raskulinecz Rd, Carteret, NJ 07008 | DE | NJ |
   | DE Warehouse | truck_transfer | 201 Gabor DR, Newark, DE 19711 | NJ | DE |
   | JFK Warehouse | truck_transfer | 14502 156th St, Jamaica, NY 11434 | NJ | JFK |
   | NJ仓库自提留存 | self_pickup | 120 Raskulinecz Rd, Carteret, NJ 07008 | NJ | *(null)* |

2. **Field-hint text**: `jsonb_set` updates (technique from
   V9) on
   `field_hints.warehouse_code` for `uchoice_inbound_request`,
   `uchoice_outbound_request`, `view_storage_history`, `view_invoice`, and
   `upsert_address`, from "JFK or DE" to "JFK, DE, or NJ". Also
   **`view_storage`'s field hint**, missed in the first draft: "Omit for
   both warehouses" → "Omit for all warehouses."

### Hardcoded text
- `core/pre_confirm_validators.py:322`
  — now built from `sorted(VALID_WAREHOUSE_CODES)` (Part 1's fix), so this
  needs no separate edit once Part 1 lands; if Part 1 and Part 3 ship
  independently, this migration includes the fallback literal-string
  edit too.
- `core/kefu_turn_apply.py:35` — same, covered by
  Part 1's dynamic-prompt fix.
- `api/admin/invoices.py:27` and
  `docs/operations/admin-api.md:149,353` —
  covered under Part 1's doc-fix bullet (same edit class); if Part 3 ships
  alone, include here instead.

### Explicitly not needed
No warehouse-code dropdown exists (admin panel field is free text), no
`uchoice_storage`/`uchoice_storage_fee_ledger` seed rows (both populate
lazily), and the pre-existing NJ-state customer *delivery* addresses in the
seed data are unrelated (different concept) and untouched.

---

## Verification

1. `pytest -m "not postgres and not live"` stays green throughout (baseline:
   362/362 passing before this work).
2. Against the local Postgres test DB
   (`docs/testing/local-postgresql.md`):
   `python scripts/apply_migrations.py` through V24, then `pytest -m postgres`
   — including the new concurrency test, which requires two real concurrent
   connections/transactions (cannot be simulated with SQLite or a single
   session) to actually exercise the `with_for_update()` fix.
3. **Bootstrap check** (per review and this project's own migration rule 7
   in `docs/operations/migrations.md`):
   apply V1 through V24 against a fresh, empty database and confirm it
   succeeds cleanly — the array rename and the new service/workflow seeds
   are exactly the kind of migration that can silently assume prior manual
   state.
4. Manual smoke via the admin panel: assign a warehouseman two warehouse
   codes, confirm a matching-warehouse Kefu case is authorized and a
   mismatched one is denied, and confirm their completion-candidate list
   excludes a third, unassigned warehouse's requests; submit an outbound
   request destined to "NJ Warehouse" from a JFK-scoped session; submit and
   confirm an inbound request, cancel it as the owner, then attempt (and
   fail) to cancel it again as a different customer, then as an admin from
   a *different* group (also denied).
