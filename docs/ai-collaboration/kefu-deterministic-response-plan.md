# Kefu deterministic operational response plan

**Version:** v2 proposal (Codex internal draft; independently reviewed in real
Claude Code round 114)  
**Status:** signed by Codex in round 110 and independently reviewed/signed by
the real Claude Code session in round 114; awaiting separate user authorization
before any production or test implementation begins.

## 1. Goal and invariants

This plan implements the goal jointly confirmed in rounds 105–106:

- AI performs semantic interpretation only.
- Backend code validates facts, changes state, and renders every operational
  Kefu response.
- Authorized U-Choice services use one shared address book.
- Outbound availability and fulfillment conserve total boxes across pallet
  buckets and permit repalletization.
- An add-address transition is described only after it really occurred.
- Only genuinely warehouse-completed inbound/outbound requests produce Kefu
  completion notices.

Standing invariants from the signed migration remain mandatory:

1. One adapter-owned transaction covers Kefu business state, audit rows,
   execution ledger, durable delivery enqueue, and notice shown-mark.
2. No Kefu path calls a Smart Robot helper that commits internally.
3. Duplicate msgids and duplicate confirmations replay or deduplicate without
   repeating business work.
4. Candidate IDs from AI are untrusted until validated against the exact list
   supplied for that turn.
5. Confirmation-time inventory decisions are made under database row locks;
   no inventory bucket becomes negative.

## 2. Scope and compatibility

### In scope

- Kefu case routing, collection, validation, confirmation, and execution text.
- Structured AI output for Kefu.
- Shared U-Choice address candidate injection and semantic matching.
- U-Choice outbound request feasibility and outbound-completion fulfillment.
- Kefu-native outbound-to-add-address pivot.
- Kefu completion-notice eligibility.
- Offline, real-Postgres, rollback, replay, and concurrency coverage.

### Explicit non-goals

- Rewriting Smart Robot's user-visible replies in this phase.
- Removing `customer_id` columns or historical customer rows.
- Reserving inventory when an outbound request is submitted. Requests remain
  non-reserving; stock is authoritatively rechecked when the warehouse records
  actual fulfillment.
- Automatically resuming the cancelled outbound draft after a new address is
  added. Staff submit a fresh outbound request after address creation, matching
  current product behavior.
- Changing Kefu transport, callback crypto, PDF determinism, or registration.

### Smart Robot compatibility rule

`AIResponse.reply` remains an optional legacy field while Smart Robot still
uses it. Prompt construction selects a channel mode:

- `source_channel == "kefu"`: structured-only contract; generated prose is
  ignored even if a provider returns it.
- `source_channel == "smart_robot"`: current reply contract and current
  Smart Robot pivot remain operational.

Shared box-level storage primitives may be used by both channels, but existing
Smart Robot tests must stay green. No old migration is rewritten.

## 3. Structured AI contract

Extend `AIResponse` with structured semantic evidence while preserving legacy
fields during the compatibility period:

```python
AIResponse(
    intent: str,
    service_type_name: str | None,
    extracted_fields: dict,
    semantic_issues: tuple[SemanticIssue, ...],
    address_match: AddressMatch | None,
    reply: str = "",                 # legacy Smart Robot only
    all_fields_collected: bool = False,  # hint only; backend never trusts it
)
```

`SemanticIssue` contains a stable code (`ambiguous_value`, `unknown_value`,
`contradictory_value`) plus field name and supplied candidate IDs/values.

`AddressMatch` is one of:

```json
{"status":"matched", "candidate_ids":["<one supplied UUID>"]}
{"status":"ambiguous", "candidate_ids":["<supplied UUID>", "<supplied UUID>"]}
{"status":"unmatched", "new_address":{"company_name":"...", "addr":"..."}}
{"status":"not_provided", "candidate_ids":[]}
```

Rules:

- `matched` must contain exactly one candidate ID.
- `ambiguous` must contain at least two candidate IDs.
- Every candidate ID is checked against the exact turn-local candidate set.
- Invalid/hallucinated IDs become a deterministic invalid-semantic-result
  outcome; they are never persisted.
- Backend schema and validators determine missing fields and readiness;
  `all_fields_collected` cannot bypass them.
- Kefu never delivers `AIResponse.reply`.
- The prompt removes the current instruction to announce cancellation/pivot
  and asks only for structured evidence.
- Parser failure produces a structured parse-error outcome, not fallback AI
  prose.
- During transition, legacy `unmatched_new_address` is parsed only for Smart
  Robot. Kefu consumes `address_match` exclusively.

## 4. Deterministic Kefu outcomes and renderers

Kefu orchestration returns a **closed discriminated union**, not a generic
code/dictionary:

```python
class OutcomeCode(Enum): ...  # every supported outcome, no arbitrary strings

@dataclass(frozen=True)
class MissingFieldsOutcome:
    code: Literal[OutcomeCode.MISSING_FIELDS]
    service_label: str
    fields: tuple[FieldPrompt, ...]

@dataclass(frozen=True)
class InsufficientStockOutcome:
    code: Literal[OutcomeCode.INSUFFICIENT_STOCK]
    warehouse: WarehouseCode
    shortages: tuple[StockShortage, ...]

KefuOutcome = MissingFieldsOutcome | InsufficientStockOutcome | ...
```

Each payload type/factory validates required facts (non-empty shortages,
positive counts, approved warehouse/field/status values). Construction fails
before rendering if facts are missing or contradictory. The renderer uses an
exhaustive `match`/`assert_never`; unknown codes or payload shapes are
programming errors, never best-effort prose.

`core/kefu_response_renderer.py` is the only operational-text renderer.
Payloads contain backend-validated facts; database labels are resolved before
construction, and raw UUIDs/internal service names are not exposed.

Renderer families (approximately 18 core functions, not one template per
service) are:

1. Routing: service list, unrecognized request, unavailable service.
2. Access/case: permission denied, no case, closed case, stale/concurrent case.
3. Collection: missing fields, invalid value, unknown catalog value,
   contradictory value, accepted correction.
4. Candidate selection: one confirmed match, ambiguous choices, no eligible
   candidate.
5. Address: matched address, unmatched-without-pivot-permission, successful
   committed pivot.
6. Inventory: insufficient initial stock, stock changed at fulfillment,
   inconsistent inventory.
7. Confirmation: deterministic summary, cancelled, already processed, nothing
   pending.
8. Execution: submitted/processing, completed, retryable failure, permanent
   failure.
9. Query result framing: empty, single, list, invalid filters, using the
   existing deterministic `result_message` section builders for data rows.
10. Completion notice: inbound completed and outbound completed only.

Renderers compose reusable sections (acknowledgement, collected summary,
validation result, missing-field questions, next action). Service metadata
supplies Chinese labels and allowed choices. Snapshot tests cover wording and
ensure no internal IDs, AI prose, or unsupported state claim appears.

Registration/transport messages already hardcoded outside the operational case
path remain unchanged unless an integration test proves they traverse the new
outcome boundary.

### Expected outcomes versus exceptions

- Expected business conditions—including insufficient stock, stock changed,
  ambiguous/unmatched address, permission denial, invalid fields, duplicate
  confirmation, and pivot unavailable—return a typed outcome. The caller
  commits precisely the case/log/audit/delivery state defined for that outcome.
- Provider exhaustion/invalid semantic JSON returns a typed
  `SemanticUnavailableOutcome` before business mutation and renders a fixed
  retry message.
- Unexpected programming, database, or invariant exceptions are never turned
  into a business outcome and never parsed for renderer facts. They propagate
  to the existing outer rollback/retry path. If retry policy is exhausted, a
  separately constructed, fixed infrastructure-failure delivery may be sent by
  that outer boundary; it contains no exception text and cannot claim that
  business work committed.
- Renderers never inspect exception strings, ORM state, or the database.

## 5. Shared U-Choice address matching

1. `session_manager._build_uchoice_candidates()` injects the shared address
   list immediately for authorized `uchoice_outbound_request` and
   `upsert_address` turns, whether or not a customer is locked.
2. `uchoice_context.address_candidates()` retains an optional filter only for
   legacy/non-Kefu callers that explicitly request it; Kefu passes no customer
   filter.
3. Candidate payload contains the stable ID and fields required for semantic
   matching/display. `customer_id` is omitted. Notes may be included only if
   they are approved address aliases; unrelated/internal notes are excluded.
4. Backend validates the AI address result:
   - one valid `matched` ID: persist `destination_address_id`;
   - valid `ambiguous` IDs: render numbered candidates and keep collecting;
   - `not_provided`: render the destination missing-field question;
   - `unmatched`: retain sanitized `company_name`/`addr` only for possible
     pivot; never fabricate an ID.
5. Customer attribution remains required for Kefu
   `uchoice_inbound_request`/`uchoice_outbound_request` because it identifies
   whom the request is for in reporting, customer copy, and audit. It is **not**
   required for shared `upsert_address`, and it is never a prerequisite for
   seeing/matching addresses.
6. For outbound, deterministic stock rejection runs as soon as SKU, quantity,
   and warehouse are resolved—even if customer attribution is still missing.
   If stock is sufficient and the destination matches, the backend separately
   asks for the missing customer before confirmation. If the destination is
   unmatched and a pivot is authorized, address maintenance can start without
   first collecting a customer because the shared address itself has no
   customer ACL.
7. Destination company/address text is never copied into `customer_id` and is
   never treated as evidence of the requesting customer. Customer matching
   uses only the separately supplied customer candidate directory.

No schema migration is needed to make existing seeded addresses shared. A
fresh-database test must prove all intended seeded addresses exist after the
full migration chain and are returned to an authorized Kefu outbound turn.

## 6. Box-level outbound feasibility and fulfillment

### 6.1 Canonical concepts

Keep three concepts separate:

- **Requested packing:** original `sku_lines`, e.g. two pallets at 72 boxes
  each. This is what the requester wants delivered.
- **Actual final packing:** warehouse `fulfillment_lines`; defaults to requested
  packing but may be explicitly corrected by warehouse staff.
- **Source picks:** concrete inventory buckets consumed to supply the actual
  total boxes, e.g. 80 boxes from one bucket plus 64 from another.

For each line:

```text
requested_boxes = pallet_count * boxes_per_pallet
              or box_count for loose input
```

Lines for the same SKU are summed for feasibility. “Compatible” means the same
warehouse and SKU; stored `boxes_per_pallet` is a source layout, not a product
identity.

### 6.2 Initial request check

After warehouse resolution/default and basic SKU/positive-integer validation:

1. Sum `boxes_per_pallet * pallet_count` across every positive storage bucket
   for each requested SKU.
2. Compare with requested total boxes.
3. If insufficient, cancel/reject the draft in the Kefu turn transaction and
   render requested versus available boxes.
4. If sufficient, continue to address handling and confirmation.

This check does not reserve stock and must not promise later availability.

### 6.3 Fulfillment allocation and atomic revalidation

At `confirm_outbound_completion` execution:

1. Derive actual required boxes from `fulfillment_lines` or requested packing.
2. Lock all positive source buckets for affected `(warehouse, sku)` rows in a
   deterministic `(warehouse, sku, boxes_per_pallet)` order.
3. Recompute availability under those locks.
4. If insufficient, handle it as a committed business outcome with **no
   inventory mutation**:
   - the original outbound request remains `processing` with no
     `completed_at` and no completion-notice eligibility;
   - the completion session moves from `pending_confirmation` back to
     `active` in the same transaction;
   - computed picks and the prior `fulfillment_lines` are cleared so a later
     staff turn must supply corrected actual fulfillment rather than silently
     reusing the rejected quantity;
   - the confirm message and deterministic stock-changed reply are appended,
     case revision advances once, and the durable reply is enqueued;
   - the logical `kefu-confirm:{session_id}:{old_revision}` execution claim is
     finalized `completed` with `db_committed_at`/`completed_at`, certifying the
     handled state transition but not inventory work. A duplicate of that
     confirm replays/deduplicates; it cannot retry the old quantity;
   - a corrected later turn creates a new pending-confirmation revision and
     therefore a new logical confirmation key.
5. Otherwise allocate deterministic picks (small/partial buckets first, then
   larger buckets) unless warehouse-supplied picks were given.
6. Validate explicit picks: real locked bucket, positive counts, no duplicate
   overdraw, and sum exactly equal to actual required boxes per SKU.
7. Consume source picks with `apply_loose_pick`-equivalent box arithmetic.
   Whole source pallets disappear; a partial pick removes one source pallet
   and creates exactly one leftover bucket. Total origin boxes decrease by the
   actual shipped total, never by a pallet-count approximation.
8. For an internal transfer, add inventory at the destination using **actual
   destination packing**, not source bucket shapes. The contract is:
   - palletized actual fulfillment may default destination packing to that
     same positive `{sku_code, boxes_per_pallet, pallet_count}` layout;
   - a loose `box_count` or source-pick-only fulfillment cannot be defaulted;
     staff must provide `destination_packing_lines`, each with positive
     `boxes_per_pallet` and `pallet_count`;
   - per-SKU destination-packing box totals must exactly equal per-SKU source-
     pick/actual-shipped totals before confirmation is shown;
   - external shipments may remain loose and omit destination packing because
     no destination inventory bucket is created.
   Origin consumption and destination addition are separate operations,
   preventing current source-layout leakage.
9. Persist requested packing on the original session, and actual packing plus
   source picks in completion result/audit JSON.

No new relational table is required: existing JSONB session/log result fields
carry these distinct structures. If implementation discovers that replay or
auditing cannot be made unambiguous without schema, work stops and the plan is
reopened rather than adding an unreviewed migration.

### 6.4 Concurrency and global lock semantics

Outbound requests do not reserve inventory, so two requests may both pass the
early check. At fulfillment, one shared storage-lock protocol applies to **all
U-Choice storage mutation paths**, not only this handler:

1. Before any row mutation, compute every affected logical `(warehouse, sku)`
   scope across origins and destinations.
2. Acquire transaction-scoped PostgreSQL advisory locks for those scopes in
   one global sorted order. A scope lock deliberately covers every pallet
   bucket for that warehouse/SKU and therefore also serializes creation of an
   absent destination bucket.
3. Only after all scope locks are held, `SELECT ... FOR UPDATE` every existing
   bucket in those scopes ordered by `(warehouse, sku, boxes_per_pallet)`.
4. Recompute, allocate, and mutate. The unique bucket key remains the final
   integrity backstop, not the primary concurrency mechanism.

Single-scope primitives acquire the same advisory scope lock. Multi-scope
handlers must predeclare/acquire the complete sorted scope set before calling
them; incremental origin-then-destination acquisition is forbidden. Thus a
JFK→DE and DE→JFK transfer acquire the same two scope locks in the same order,
and concurrent creation of the same absent destination bucket is serialized.
The first competing fulfillment may succeed; the second observes the new
total and returns the handled stock-conflict state above, with no partial or
negative rows.

## 7. Genuine Kefu add-address pivot

The Kefu pivot is implemented inside `kefu_turn_apply`'s caller-owned
transaction; it must not call `workflow_engine._maybe_pivot_to_add_address()`.

Ordering:

1. Merge and sanitize structured fields.
2. Resolve/default warehouse and validate SKU/quantity.
3. Run total-box feasibility.
4. If insufficient, terminate with inventory rejection; ignore unmatched
   address evidence for state-transition purposes.
5. If sufficient, validate address-match evidence.
   Customer attribution is handled independently: it is still required before
   a matched-address outbound can reach confirmation, but it is not required
   for shared address visibility or for an authorized unmatched-address pivot.
6. For `unmatched` and an actor authorized for `upsert_address`, atomically:
   - mark the outbound draft log cancelled;
   - close its session as cancelled;
   - create a new `upsert_address` session and request log;
   - seed only sanitized `company_name` and `addr`;
   - update the adapter context/execution row/staff binding to the new session;
   - append deterministic user/assistant audit turns;
   - enqueue the deterministic pivot response in the same commit.
7. The response states that cancellation/pivot happened only after these
   pending mutations are part of the adapter-owned successful commit.
8. If the actor cannot use `upsert_address`, do not cancel the outbound draft;
   render the deterministic escalation instruction.
9. Any exception rolls back both sides: the old draft remains open and no new
   address session/log/reply survives.

The new address flow asks for only backend-computed missing fields (currently
charge type and associated warehouse when company/address were extracted).

## 8. Completion-notice eligibility

Change the lock query to join `service_type` and require:

- `request_log.source_channel = 'kefu'`;
- `request_log.status = 'success'`;
- `request_log.completed_at IS NOT NULL`;
- service name is exactly `uchoice_inbound_request` or
  `uchoice_outbound_request`;
- `completion_notice_shown_at IS NULL`;
- existing staff warehouse scope still matches.

Remove the generic direction fallback from eligible rendering. Unknown service
is treated as ineligible/error, never “request completed.” Preserve
`FOR UPDATE SKIP LOCKED`, shown-mark in the final reply transaction, and
exactly-once concurrent claim behavior.

No new completion column is proposed: an original inbound/outbound row starts
`processing` and reaches `success/completed_at` only through warehouse
completion. Tests prove that invariant against the real workflow.

## 9. Catalog migration and fresh database behavior

Add one forward-only catalog migration (next available version; expected V14)
to update U-Choice field hints/descriptions that currently describe exact
pallet-bucket matching or AI-authored replies. It does not alter historical
migrations or remove columns.

The migration must be idempotent in effect and tested after running the full
V1→latest chain. Existing production rows and addresses are preserved. No
production migration is applied during implementation without separate user
authorization.

## 10. Implementation stages

### Stage A — semantic contract and renderer foundation

- Add structured semantic/address types and channel-specific prompt contract.
- Add deterministic outcome and renderer modules.
- Unit-test parsing, hallucinated IDs, renderer families, and no-AI-prose Kefu
  guarantee.

### Stage B — shared address context

- Inject unfiltered shared U-Choice addresses for authorized Kefu services.
- Validate matched/ambiguous/unmatched outcomes in backend code.
- Preserve customer metadata without using it as an address ACL.

### Stage C — box arithmetic and catalog migration

- Introduce pure requested-box calculators and locked source-pick allocator.
- Refactor early stock check and completion storage mutation.
- Separate internal-transfer destination packing from origin source picks.
- Add the forward catalog migration.

### Stage D — Kefu orchestration and genuine pivot

- Route every Kefu operational branch through typed outcomes/renderers.
- Reorder readiness so stock validation precedes unmatched-address pivot.
- Implement the single-transaction Kefu-native pivot and rollback behavior.

### Stage E — completion notice and full integration

- Restrict notice eligibility.
- Run end-to-end incident, replay, rollback, and concurrency suites.
- Cross-review each writer's diff and rerun all offline/real-Postgres suites
  separately, following the repository's existing isolation rule.

## 11. Test and acceptance matrix

### Structured AI/rendering

- Kefu parser accepts each address status and structured issue type.
- Hallucinated/non-candidate address IDs are rejected before persistence.
- AI `reply` containing a false success/pivot/secret marker never appears in
  any Kefu delivery, case turn, or conversation history.
- Every operational branch produces a known outcome code and deterministic
  rendering.
- Every outcome payload rejects missing, wrong-type, empty, or contradictory
  facts; the renderer is exhaustive over the closed outcome union.
- Expected business failures commit only their explicitly defined state;
  injected unexpected exceptions roll back and enter retry without a renderer
  examining exception text.
- Missing/invalid/ambiguous questions list exact actionable fields/choices.
- Smart Robot legacy reply behavior remains unchanged.

### Shared addresses

- Fresh DB contains the expected seeded address rows.
- First-turn authorized Kefu outbound receives shared candidates without a
  locked customer.
- Addresses with different/null `customer_id` are all matchable.
- Outbound/inbound still require separately matched customer attribution before
  confirmation, while `upsert_address` does not; destination company text can
  never satisfy that requirement.
- Existing match persists only a real candidate ID; ambiguous match persists
  none; unmatched match fabricates none.
- Unauthorized/non-U-Choice services receive no address list.

### Box-level stock

- `2 × 72 = 144` is rejected at 143 total boxes and accepted at 144 across
  multiple differently sized buckets.
- Multiple lines for one SKU aggregate correctly; multiple SKUs validate
  independently.
- Partial source picks create exact leftovers and conserve total boxes.
- Explicit picks must sum exactly to actual fulfillment boxes.
- Internal transfer removes source-pick layouts and adds actual final packing.
- Loose internal transfer cannot confirm without explicit positive
  `destination_packing_lines`; mismatched source/destination box totals fail
  validation. An external loose shipment needs no destination packing.
- Stock changing between request and warehouse completion is caught under lock.
- A handled stock conflict leaves the original request `processing`, returns
  the completion session to `active`, clears rejected fulfillment/picks,
  advances revision once, finalizes the old confirmation claim as `completed`,
  and allows a corrected later revision to be confirmed.
- Two simultaneous completions competing for the same boxes yield one valid
  mutation and one clean stock-changed result, with no negative/partial rows.
- Opposing JFK→DE / DE→JFK transfers and multi-SKU reversed-order work do not
  deadlock because all origin/destination scope locks use one global order.
- Two transactions creating the same previously absent destination bucket are
  serialized and produce one correct aggregate bucket without unique-key
  failure or lost quantity.

### Pivot

- Insufficient stock plus unmatched address rejects stock and creates no
  address session.
- Sufficient stock plus matched address stays outbound.
- Sufficient stock plus ambiguous address asks deterministic selection.
- Sufficient stock plus unmatched address atomically cancels outbound and
  creates one seeded address session/log.
- No pivot permission leaves outbound open and renders escalation.
- Injected exception at every pre-commit boundary leaves old session/log,
  new session/log, case revision, binding, audit, delivery, and execution
  ledger mutually consistent (all old or all new, never half-pivoted).
- Duplicate msgid and concurrent staff turns produce at most one pivot.

### Completion notices

- Successful `view_storage`, invoice, digest, explanation, registration, and
  address maintenance never qualify.
- Completed inbound/outbound qualifies exactly once.
- Merely submitted/processing inbound/outbound does not qualify.
- Two simultaneous Kefu turns cannot both claim the same notice.
- Failure before final commit does not consume the notice.
- No generic “request completed” fallback remains.

### Incident acceptance

For the supplied S2/YANWEN message:

- service and fields are extracted;
- warehouse is resolved/defaulted;
- required boxes equal 144;
- if total S2 stock is below 144, the draft is cancelled with a deterministic
  requested/available-box response;
- no add-address flow starts and no previous request number appears;
- if stock is sufficient in a controlled variant, shared address matching runs
  and only a real unmatched result can trigger the atomic pivot.

### Full regression gates

- All existing offline tests pass.
- Kefu integration and storage atomicity suites pass separately against real
  PostgreSQL.
- The two round-102 minor follow-ups are folded in: direct terminal-case
  multi-staff binding-clear regression and stale rollout-gate comment cleanup.
- No live WeChat/OMS/YiDiDa call, production DB mutation, commit, push, or
  deployment is part of test acceptance.

## 12. Explicit writer ownership

Ownership is non-overlapping. Either agent may review but only the named writer
edits a file during implementation unless a new handoff is recorded first.

### Claude Code writer

- `ai/base.py`
- `ai/prompt_builder.py`
- `ai/openai_provider.py` and `ai/claude_provider.py` only if contract plumbing
  requires it
- `core/uchoice_context.py`
- `core/session_manager.py`
- new `core/kefu_outcomes.py`
- new `core/kefu_response_renderer.py`
- semantic/renderer/shared-address unit tests in new files:
  - `tests/kefu/test_kefu_semantic_contract.py`
  - `tests/kefu/test_kefu_response_renderer.py`
  - `tests/kefu/test_kefu_shared_addresses.py`
  - `tests/kefu_integration/test_kefu_shared_address_book.py`

### Codex writer

- `core/kefu_turn_apply.py`
- `core/kefu_case_adapter.py`
- `core/kefu_completion_notice.py`
- `core/pre_confirm_validators.py`
- `core/uchoice_storage.py`
- `handlers/uchoice/storage_txns.py`
- the next forward catalog migration under `db/migrations/`
- orchestration/storage/notice tests in new or existing files:
  - `tests/kefu/test_kefu_box_feasibility.py`
  - `tests/kefu/test_kefu_address_pivot.py`
  - `tests/kefu_integration/test_kefu_box_fulfillment.py`
  - `tests/kefu_integration/test_kefu_address_pivot.py`
  - `tests/uchoice_self_registration/test_kefu_completion_notice.py`
  - `tests/kefu_integration/test_kefu_process_turn_crash_recovery.py`
  - a new direct terminal-binding-clear regression file

### Integration contract between writers

Claude publishes before Stage D:

```python
validate_address_match(ai_response, candidates) -> AddressDecision
render_kefu_outcome(KefuOutcome) -> str
```

Codex owns construction of `KefuOutcome` from real orchestration state and
never imports renderer internals. Claude's renderer never queries or mutates
the database. Both agents may implement Stages A–C in parallel after user
authorization; Stage D waits for the two public interfaces above. Each agent
then cross-reviews the other's owned diff before the plan can be called done.

## 13. Authorization gates

1. Codex reviews/challenges this v2 proposal.
2. Claude Code incorporates accepted corrections and both agents explicitly
   sign one version.
3. The user separately authorizes implementation.
4. Implementation proceeds under Section 12 ownership.
5. Commit, push, production migration, deployment, or live API action each
   remains outside implementation authorization unless the user explicitly
   requests it.

## 14. Signatures

- **Codex:** signed v2 without technical objection in discussion round 110.
- **Claude Code:** independently reviewed the goal, repository, and exact v2
  plan from scratch and signed without blocking objection in discussion round
  114. Round 111's purported Claude signature was authored by a mislabeled
  Codex subagent and carries no independent-review weight.

These signatures approve the plan only. They do not authorize implementation,
tests that modify project code, migration execution, commit, push, deployment,
database mutation, or operational API calls.

## 15. Independent-review implementation notes

Claude Code round 114 recorded two non-blocking requirements that are part of
implementation acceptance:

1. A handled stock conflict clears `destination_packing_lines` together with
   computed source picks and prior `fulfillment_lines`; rejected destination
   packing must never be silently reused on a corrected revision.
2. Section 12 deliberately transfers ownership of
   `core/kefu_turn_apply.py` and `core/kefu_case_adapter.py` from Claude Code's
   original Kefu migration ownership to Codex for this phase. This is an
   explicit phase-specific transfer, not an accidental conflict with the
   earlier signed migration plan.
