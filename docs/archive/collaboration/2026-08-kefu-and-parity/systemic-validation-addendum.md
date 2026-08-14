# Systemic validation addendum

Status: **Signed by both agents (Claude Code's round-15 revision; Codex's
round-16 final verification and sign-off) and APPROVED by the user**, given
in chat with Claude Code alongside approval of Phase 1 and Phase 3. §4's
SKU-substitution policy is resolved: **no substitution** — a completion may
only adjust quantity/packing for SKUs already on the original request, not
introduce a different product. Implementation may now begin, per the
fixture-first sequencing in §6/§7 (pre-implementation tests first, encoded
against current behavior). This document is **additive** to `agreed-plan.md`
— it
does not modify, reopen, or contradict that document's sections 1-7 (the
signed outbound `uchoice_outbound_request` plan, which is treated here as
**Phase 1**). It is also additive to, and coordinates with,
`phase3-outbound-pdf-timing.md` (**Phase 3**, drafted round 15 per explicit
user direction) — §3b/§5 below have been updated to make clear the outbound
instruction PDF is *not* part of this addendum's target design for
`confirm_outbound_completion`, even though the current-state trace still
shows where it sits today. Nothing in this addendum authorizes any
application-code change; the same two-stage authorization rule applies
(pre-implementation test work is allowed now; implementation begins only
after the user separately approves this addendum).

Synthesized from `discussion.md` rounds 9-16. Where this document and
`discussion.md` differ in wording, `discussion.md` is the source of record.

## 1. Scope and relationship to Phase 1

Phase 1 (`agreed-plan.md`) covers `uchoice_outbound_request` only. This
addendum (**Phase 2**) extends the same class of fix — a validation boundary
between AI-supplied identifiers and persisted/executed state — to six more
service paths, discovered by a systematic audit of all 15 `service_type`
rows (not 16 — corrected in round 10) for the same pattern: does a field
naming a candidate/catalog entity (`sku_code` above all) get validated
before it's trusted.

| Service | Field | Required/Optional | Existing pre-confirm validator |
|---|---|---|---|
| `adjust_storage` | `adjustment_lines[].sku_code` | required | none |
| `move_storage` | `move_lines[].sku_code` | required | none |
| `recount_storage` | `inventory_lines[].sku_code` | required | none |
| `uchoice_inbound_request` | `sku_lines[].sku_code` | required | none |
| `confirm_inbound_completion` | `received_lines[].sku_code` | optional | `_loose_inbound_restatement_required` (loose-line presence only, not SKU membership) |
| `confirm_outbound_completion` | `fulfillment_lines[].sku_code` | optional | `_loose_outbound_pick_required` (loose-line presence only, not SKU membership) |

This addendum also covers a **second, more severe defect** found during
verification (round 10, confirmed round 11): a **transaction-atomicity gap**
in the shared storage-mutation utility, independent of SKU validation and
higher priority.

## 2. Root causes

### 2a. No shared SKU/catalog validation layer (extends Phase 1's root cause)

Same structural gap as `agreed-plan.md` §1: the model's raw output is
trusted as workflow state without a validation boundary. Phase 1 addressed
this for `uchoice_outbound_request` specifically; this audit confirms it is
systemic, not outbound-specific. `PRE_CONFIRM_VALIDATORS`
(`core/pre_confirm_validators.py:164-169`) has 4 entries; none validate
`sku_code` catalog membership anywhere.

The failure is not silent data corruption — `uchoice_storage.sku_code` has
a real `FOREIGN KEY` constraint to `uchoice_sku`
(`db/migrations/V1__initial_schema.sql:765-766`), so a fabricated code is
eventually rejected by the database. But:

- It fails **after confirmation**, not before — generically, via a broad
  `except Exception` in `_execute_workflow_and_finish`
  (`core/workflow_engine.py:791-797`) that shows
  "申请处理失败，请稍后重试或联系管理员" with no specifics.
- `handlers/uchoice/storage_txns.py` accesses `line["sku_code"]` directly
  (not `.get()`) in several places — a genuinely *missing* key crashes with
  a Python `KeyError` even before reaching the database, hitting the same
  generic catch-all.
- `uchoice_storage_txn.sku_code` has **no** FK (only `uchoice_storage.sku_code`
  does) — confirmed round 11.

### 2b. Transaction atomicity gap (new, higher priority, independent of 2a)

`apply_storage_delta` (`core/uchoice_storage.py:13-69`) calls `db.commit()`
internally, on every single invocation (line 68) — confirmed by direct read,
round 10 and round 11. Multi-delta handlers call it repeatedly per line:
`MoveStorageHandler` calls it **four times per line**
(`handlers/uchoice/storage_txns.py:220-227` — `move_out`/`move_in` for the
source bucket, then `move_out`/`move_in` for the target bucket), each
independently committed.

**Consequence**: if the 3rd of 4 calls fails — a *decrement*, removing 1
pallet from the target bucket at its original `boxes_per_pallet`, which can
fail if that exact bucket is missing or has insufficient stock (corrected
round 12-13; a positive delta always succeeds by creating/updating the
bucket, so it's never "can't absorb the increment") — the first two are
already permanently committed: the source bucket is short one pallet with
nothing correspondingly created anywhere. This is real inventory loss, not
merely a bad error message. The broad exception handler in
`_execute_workflow_and_finish` marks the request `failed` but has no
mechanism to undo already-committed deltas.

**Secondary effect, confirmed round 11**: `apply_storage_delta` takes a
`with_for_update()` row lock (`core/uchoice_storage.py:32`) on the bucket it
touches. Committing after every delta releases that lock immediately rather
than holding it for the whole multi-delta operation — a concurrent second
workflow touching the same bucket mid-`move_storage` is only protected
delta-by-delta, not for the operation as a whole. Fixing atomicity fixes
this too, as a side effect.

This gap applies to every service that calls `apply_storage_delta` more
than once per confirmed operation. Per the completed call-site sweep (§3b):
`move_storage` (4x/line), `adjust_storage`/`recount_storage` (1x per
affected line/bucket, exposed whenever a request has more than one line),
`confirm_inbound_completion` (1x/line), `confirm_outbound_completion`
palletized (up to 2x/line), and — the actual worst case, corrected round
12-13 — `confirm_outbound_completion` loose lines via `apply_loose_pick`,
which can issue **up to 5 deltas per pick** (whole pallets, a remainder
conversion, and an internal-warehouse destination combined). `move_storage`
is not the worst case; it was an earlier, incomplete estimate before the
full sweep.

## 3. Design

### 3a. Shared SKU/catalog validation primitive (narrow scope, per Codex's round-10 correction)

Rejected: one large `validate_sku_lines(lines, warehouse_code, db)` bundling
stock-relevance, snapshot-vs-delta semantics, and box-conservation — these
differ too much per service to share one function without over-fitting.

Adopted instead — a small primitive validating only **invariant facts**,
true regardless of which service is calling it:

- the line is an object (not a bare string/number/etc.);
- `sku_code` is present, a non-empty string, and exists in `uchoice_sku`;
- duplicate identifiers/buckets are reported where the calling contract
  forbids them;
- errors carry stable field paths (e.g. `inventory_lines[2].sku_code`) so
  callers can render a specific, targeted message rather than a generic one.

Each service composes this primitive with its own typed rules:

- **`uchoice_inbound_request`**: palletized-vs-loose union, positive
  quantities; no current-stock requirement (this is new stock arriving, not
  being drawn down).
- **`adjust_storage`**: valid bucket dimensions, non-zero integer delta, no
  negative resulting balance.
- **`recount_storage`**: non-negative snapshot counts, unique bucket keys,
  explicit full-snapshot semantics (a bucket omitted from the snapshot is
  treated as now zero, not unchanged — per the service's own field_hint).
- **`move_storage`**: both source and target buckets exist, quantities are
  positive/in-range, box conservation holds (nothing enters or leaves the
  warehouse, only moves between buckets).
- **`confirm_inbound_completion` / `confirm_outbound_completion`**: validate
  the **effective lines** — override lines when the warehouseman provided
  them, otherwise the original request's lines — not merely the optional
  override field in isolation. A completion turn with no override still
  needs its inherited lines validated.

**Validation boundary placement** (per Codex's round-10 point, agreed):
pre-confirm validation is necessary but is defense in depth only. The
primary boundary must sit *before* model output merges into persisted
`collected_fields` — matching what's already agreed for outbound in
`agreed-plan.md` §4 — otherwise invalid state is stored and can become
context fed back to the model on a later turn. Execution must validate
*again* against current authoritative state regardless, since inventory can
change between confirmation and the user's confirm reply (same principle as
Phase 1's `execution_plan` transactional revalidation).

The `line["sku_code"]` → `.get()` dict-access fix is retained in this
addendum but explicitly **deprioritized** below the atomicity fix — per
Codex's round-10 point, `.get()` alone only moves the crash to a later
database/arithmetic operation; each handler still needs to raise a specific,
controlled error if validated input is somehow missing at that point, not
just avoid the `KeyError`.

### 3b. Atomic workflow transactions, split into a DB phase and a post-commit side-effect phase (revised round 13 per Codex's round-12 correction)

**Correction to the original design**: "commit once at the end of the
workflow" is unsafe as stated, because `_run_workflow_steps` does not run
DB-only work — for the completion services specifically, confirmed by
direct query against `workflow_step` (round 13):

```
confirm_inbound_completion:  lookup_and_validate_completion → apply_inbound_storage_txn
                              → generate_pdf_stub → complete_existing_request → reply_wechat
confirm_outbound_completion: lookup_and_validate_completion → apply_outbound_storage_txn
                              → generate_pdf_stub → complete_existing_request → reply_wechat
```

`generate_pdf_stub` and `complete_existing_request` run *after* the storage
mutation but *before* the loop ends — and `complete_existing_request`
performs a real external call, confirmed by direct read of
`handlers/uchoice/complete_request.py:16-23`
(`clients.wechat_client.send_group_webhook_message`, a cross-group webhook).
Every affected workflow ends with `reply_wechat`, which sends the final
WeChat reply. Holding an open DB transaction and row locks across those
external calls is unsafe on its own; more fundamentally, a DB rollback
cannot unsend a webhook or a WeChat message — so a single commit-at-the-end
can't correctly express "the notification failed but the inventory
operation should still count as successful," which is the correct outcome,
not an error state.

**Target-state note (added round 15, per explicit user direction — see
Phase 3):** the step trace above describes the *current, present-day*
workflow shape, and remains an accurate description of `confirm_inbound_completion`
going forward — its `generate_pdf_stub` step (the inbound receiving
document) is unaffected by this note. For `confirm_outbound_completion`
specifically, this trace is **not** the target state: the outbound
pickup/delivery **instruction** PDF is moving to `uchoice_outbound_request`'s
own workflow, generated once, post-commit, right after the customer's
request is created — not regenerated at warehouse-completion time. See
`phase3-outbound-pdf-timing.md` for the full design. This §3b phase-split
mechanism (DB phase vs. post-commit side-effect phase) still applies to
`confirm_outbound_completion`'s *remaining* post-commit work (the webhook
and the final reply) once the instruction-PDF step is removed from it — the
transaction-boundary fix and the PDF-relocation are independent changes
that both touch this workflow's step sequence, tracked in separate phases
so neither implementation blocks on the other.

**Two explicit phases, not one commit point:**

1. **Transactional database phase** — revalidate authoritative state,
   compute the full operation plan, apply every storage/audit delta with no
   per-call commits, transition the request's business status (the current
   `mark_success` call) — all inside **one** transaction, committed once.
   Only DB-only step types belong in this phase (e.g.
   `apply_inbound_storage_txn`, `apply_outbound_storage_txn`,
   `adjust_storage_txn`, `move_storage_txn`,
   `lookup_and_validate_completion` as a read-only precondition check).
2. **Post-commit side-effect phase** — runs only after phase 1's commit
   succeeds, with no DB row locks held: `generate_pdf_stub`,
   `complete_existing_request` (webhook), `reply_wechat`. A failure here is
   logged/reported as a **delivery failure**, separate from the inventory
   operation's outcome — it must not roll back or relabel already-committed
   inventory state. This addendum does not mandate a full outbox/idempotency
   design for exactly-once delivery; that's a larger feature explicitly out
   of scope here, but the addendum must not claim DB transactions cover
   external effects, since they can't.
3. Remove `db.commit()` from `apply_storage_delta` itself; flush as needed
   inside the helper, commit once at the phase-1 boundary.
4. A phase-1 failure rolls back all inventory/audit/business-state changes
   from that attempt, then records the failure in a fresh transaction (since
   the failed transaction can't be reused to write the failure record).
5. A phase-2 failure does **not** roll back or mark the inventory operation
   failed — it's recorded/surfaced as a separate delivery-failure concern.

**Mechanism**: introducing this split requires classifying each
`step_type` as DB-phase or side-effect-phase (e.g. a small lookup set in
`core/workflow_engine.py`, checked when iterating `_run_workflow_steps`),
and moving `mark_success` to fire at the end of phase 1 rather than after
the whole step loop as it does today. Exact mechanism (a step-level flag on
`WorkflowStep` vs. a hardcoded classification table) is left to the
implementing agent — Codex's point stands that this affects
`core/workflow_engine.py` itself, not only `core/uchoice_storage.py` and the
mutation handlers (see §5's revised ownership note).

**Tests** (both boundaries, not just "the request is marked failed"):

- Inject a phase-1 failure (on line 2 of a multi-line request, and on each
  of `move_storage`'s 4 per-line deltas, and on `apply_loose_pick`'s up to 5
  per-pick deltas — see the corrected call-site sweep below) and assert
  every balance and audit (`uchoice_storage_txn`) row is unchanged, and the
  request is *not* marked successful.
- Inject a phase-2 failure (e.g. the webhook or WeChat reply throwing) after
  a successful phase-1 commit, and assert the inventory state and request
  status remain the successful, committed result — the delivery failure is
  recorded/reported separately, not treated as an operation failure.

**Wording correction to the `move_storage` failure example** (round 12):
the realistic 3rd-call failure is a *decrement* (removing 1 pallet from the
target bucket at its original `boxes_per_pallet`, which can fail on missing/
insufficient stock at that exact bucket) — not "the target bucket can't
absorb the increment." A positive delta in `apply_storage_delta`
(`core/uchoice_storage.py:35-47`) always succeeds by creating or updating
the bucket; only a negative delta can raise `库存不足`. Confirmed by direct
re-read of the function's own logic.

**Completed call-site sweep** (resolves the open item from §7 of the prior
draft): repository-wide grep confirms `apply_storage_delta` has no callers
outside `handlers/uchoice/storage_txns.py` and `core/uchoice_storage.py`'s
own `apply_loose_pick`. Exposure by service, confirmed via direct read of
each call site:

| Service | Deltas per unit of work |
|---|---|
| `confirm_inbound_completion` | 1 per line |
| `adjust_storage` / `recount_storage` | 1 per affected line/bucket |
| `move_storage` | 4 per line |
| `confirm_outbound_completion` (palletized) | up to 2 per line (internal warehouse transfer) |
| `confirm_outbound_completion` (loose, via `apply_loose_pick`) | up to **5** per pick (whole pallets + a remainder conversion + an internal-warehouse destination) — confirmed by direct count of `apply_storage_delta` call sites inside `apply_loose_pick` |

This is **higher priority than 3a's dict-access patch** — it protects
against every mid-operation DB failure, not only a missing key.

## 4. RESOLVED: completion-time SKU substitution — no substitution

Was a business/domain policy question not resolvable from repository
evidence alone: **may a warehouseman substitute an entirely different SKU
during physical completion** (e.g. the request said Product A, but they
actually shipped/received Product B), or **may they only change
quantities/packing for SKUs already on the original request**?

**User decision**: no substitution. Completion lines may only adjust
quantity/packing for SKUs present on the original request; a physically
different product being shipped/received is not representable as a
completion-line edit. If a genuine substitution case arises later, it needs
its own explicit rule and confirmation-rendering treatment — not an
accidental side effect of the validator simply accepting any real catalog
SKU on a completion line. This is now normative for the two completion
services' contract tests (owned by Codex, see §5).

## 5. Work division

Accepted as proposed (round 10), grouped by genuine shared-file dependency
to avoid both agents editing the same production files:

- **Claude Code**: contract matrices + baseline tests for `adjust_storage`,
  `move_storage`, `recount_storage`, including partial-commit/rollback
  fixtures per §3b. **Single writer** (after user approval) for the
  transaction-boundary changes in `core/uchoice_storage.py`, the mutation
  handlers' adaptation to it, **and the DB-phase/side-effect-phase split in
  `core/workflow_engine.py`** (`_run_workflow_steps`/
  `_execute_workflow_and_finish`) — extended per Codex's round-12 point that
  the phase boundary is introduced in the engine, not only in the storage
  helper, so ownership of transaction-boundary work must include whichever
  file that boundary actually lives in.
- **Codex**: contract matrices + baseline tests for
  `uchoice_inbound_request`, `confirm_inbound_completion`,
  `confirm_outbound_completion`, including effective-line fallback,
  substitution-policy fixtures (pending §4's resolution), and phase-2
  delivery-failure fixtures for the two completion services specifically —
  `confirm_inbound_completion`'s receiving-document `generate_pdf_stub` plus
  both services' `complete_existing_request` webhook; **not** an outbound
  instruction-PDF fixture for `confirm_outbound_completion`, since that step
  is relocating per Phase 3 (`phase3-outbound-pdf-timing.md`). **Single
  writer** (after user approval) for the shared typed/catalog validation
  primitive (§3a) and the `PRE_CONFIRM_VALIDATORS` composition point.
- Each agent reviews the other's tests and proposed diff before either
  begins their own single-writer implementation work. Since
  `core/workflow_engine.py` is touched by Claude Code's phase-split work
  here *and* already carries Phase 1's outbound-specific logic
  (`_outbound_required_fields_present`, the continuation-routing invariants,
  etc.), Codex's review of that specific diff should explicitly check it
  doesn't disturb any already-signed Phase 1 behavior in the same file.
- Test files stay separate by service group. No production writer begins
  until the user explicitly approves this addendum — this applies
  independently to Phase 1 and Phase 2; approving one does not imply
  approval of the other.

## 6. Test strategy (same fixture-first discipline as Phase 1)

Per the sequencing already agreed and corrected for Phase 1
(`agreed-plan.md` §7, Codex's round-6 catch): tests are written **first**,
encoded against **current, unmodified** behavior, and are expected to
**fail** on the two known gaps (missing SKU validation, non-atomic
multi-delta writes) — proving they test the real defects before any fix
exists. Implementation only begins after the user's separate approval, and
only after those baseline-failure tests are recorded.

## 7. Remaining disagreements or risks

None material as of round 13 — Codex's round-12 transaction-boundary
correction has been incorporated into §3b (revised) and §5 (ownership
extended to `core/workflow_engine.py`). Open items carried forward, not
disagreements:

- **§4's substitution policy** — explicitly deferred to the user, not
  resolved by either agent.
- **Exact test fixtures for the DB-phase/side-effect-phase split** (§3b) —
  scoped here, not yet written; Claude Code's next concrete step once this
  addendum is verified.
- **Exact step-type classification mechanism** (a `WorkflowStep`-level flag
  vs. a hardcoded lookup table distinguishing DB-phase from side-effect-phase
  step types) — deliberately left open per §3b, to be settled by the
  implementing agent, not blocking sign-off.
- ~~Whether any other handler beyond the six named here calls
  `apply_storage_delta` multiple times per confirmed operation~~ —
  **resolved round 12/13**: repository-wide sweep confirmed no callers
  outside `storage_txns.py` and `apply_loose_pick`; exposure table added to
  §3b.

## Sign-off

- Claude Code: this document is my draft (round 11), revised (round 13) per
  Codex's round-12 correction, revised again (round 15) per Codex's round-14
  scoped corrections, incorporating rounds 9-16 in full.
- Codex: **signed, round 16** — confirmed §2b's third-call decrement
  correction and the "up to 5 deltas per pick" worst case; confirmed §3b/§5
  clearly separate current-state PDF placement from the Phase 3 target,
  preserve inbound receiving-document scope, and retain outbound
  completion's webhook/reply as post-commit effects. The §4 SKU
  substitution policy is resolved: no substitution.

**APPROVED by the user.** Implementation may begin, per §6/§7's
fixture-first sequencing.
