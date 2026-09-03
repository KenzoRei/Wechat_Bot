# Warehouse array, cancel service, and NJ warehouse

**Status:** Historical — plan signed off, implementation follows in the
normal commit history.

This directory preserves the review process behind three related U-Choice
changes, requested together in one session on 2026-09-03:

1. Letting a warehouseman/Kefu staff member be assigned more than one
   warehouse (array column, not a join table).
2. A new service to cancel an inbound/outbound request after it's been
   confirmed but before a warehouseman completes it — a gap identified
   during initial codebase exploration (no existing path terminates a
   `processing` request other than completion or, on Smart Bot only, a
   7-day staleness sweep).
3. Adding a third warehouse, NJ, as a valid code and an internal-transfer
   destination from/to the existing JFK and DE warehouses.

## Process

The plan went through four rounds of independent technical review (an
external reviewer, referred to as Codex in the working session) before
implementation began. Each round was verified against the actual source
before being accepted or pushed back on — findings were not taken on faith.
Summary of what each round changed:

- **Round 1** caught that a warehouseman's assigned warehouse would silently
  authorize completions on the Kefu channel but not Smart Bot, that no
  service existed to cancel a confirmed-but-not-completed request, and
  scoped the NJ warehouse's internal-transfer address rows.
- **Round 2** found a release-blocking bug: both channels' finalizers
  unconditionally overwrite a target request's status right after handlers
  run, which would have silently reverted every cancellation back to
  `success`. Also found: the Kefu pipeline needed its own separate wiring
  (allowlist, candidate keys, labels, prompt instructions) entirely missed
  by only touching the Smart Bot engine; multi-warehouse candidate scoping
  falling back to `None` would leak cross-warehouse data; and no row
  locking existed to serialize a completion attempt against a concurrent
  cancellation attempt.
- **Round 3** found that the round-2 concurrency fix was necessary but not
  sufficient — both pipelines mutate a target request's status to
  `processing` *before* any lock is acquired, which reorders the race into
  a real bug regardless of the lock; that the exception path for a losing
  race would mark an already-resolved target `failed`; a boolean-logic bug
  in the reviewed plan's own `_authorize_case` translation that would have
  denied every admin/accountant; a SQLAlchemy identity-map staleness trap
  in the proposed lock (`with_for_update()` without `populate_existing()`);
  and that a raw `db.rollback()` inside the Kefu pipeline would have
  destroyed its own replay/idempotency ledger for the current turn.
- **Round 4** found the remaining authorization gap: warehouse-scope
  enforcement had only been specified for completion/cancellation, not for
  `adjust_storage`/`move_storage`/`recount_storage`/inbound-outbound
  creation/`view_storage_history`/`view_invoice`/`view_storage` — a
  pre-existing gap (present even in the original single-warehouse design)
  that needed closing before the array conversion could be considered
  complete, since the plan itself had already claimed this enforcement
  existed "at the validator/handler boundary" without specifying one.

## Files

- `agreed-plan.md` — the final, converged plan as approved for
  implementation. Each fix section documents what was wrong in the prior
  draft and why, in place of a separate review-transcript file — the
  reasoning is preserved inline rather than requiring a reader to
  cross-reference a separate discussion log.

## Outcome

Implementation follows this plan in the same repository, tracked through
the normal migration numbering (`V22`–`V24`) and commit history rather than
a separate collaboration log, since (unlike the `2026-08-kefu-and-parity`
work this repo's convention originates from) this was a single-session,
single-agent review process rather than a multi-week two-agent
collaboration.
