# Current phase: deterministic Kefu operational responses

## State

> **Round 112 correction, closed by round 114:** rounds 103, 105, 107, 109,
> 111 (attributed to "Claude Code" below) were actually written by a
> Codex-spawned subagent mislabeled `claude_code` -- disclosed by Codex,
> relayed by the user (discussion.md round 112). **Round 114 is this phase's
> first genuine independent Claude Code review**: the goal and plan v2 were
> re-verified against the current repository from scratch (not inferred from
> rounds 103-111) and confirmed, with two non-blocking findings recorded.
> The goal/plan below now carry one real cross-model signature (round 114)
> alongside Codex's own rounds 104/106/108/110.

> **Round 123 update:** implementation is COMPLETE and mutually
> cross-reviewed. The user authorized implementation in round 116; Claude
> Code's Stage A/B (round 118) and Codex's Stage C/D/E (round 122) are both
> done. Each side found and the other fixed real issues in the other's
> diff (rounds 119-121); Claude's final full-diff pass (round 123) found no
> blockers, only 2 minor non-blocking items. Commit, push, deployment,
> production migration, and operational API calls remain separately gated
> and are NOT authorized by any of this.

> **Round 124 update:** both of round 123's minor non-blocking findings are
> fixed (new `ConfirmationRecoveringOutcome` outcome/renderer replacing a
> hardcoded string; a stray indentation/stale-comment fix in
> `kefu_case_adapter.py`). Round 124 also removed a real domain-modeling
> error, corrected directly by the user: `customer_id` was wrongly required
> for `uchoice_inbound_request`/`uchoice_outbound_request` -- every current
> U-Choice service is performed on behalf of U-Choice itself, the sole
> platform tenant, and `customer_id` is dormant infrastructure for a real
> future second tenant, not something today's services should collect. See
> `decisions.md`'s "Superseded or challenged assumptions" for the full
> correction. Full offline suite 267/267, both real-Postgres suites green.
> Commit, push, deployment, production migration, and operational API calls
> remain separately gated and are NOT authorized by any of this.

Documentation reorganization (Phase 0) is complete and independently verified
(round 104, genuinely Codex). The goal is confirmed and plan v2 is signed by
both a genuine Codex review and a genuine Claude Code review (round 114).
Implementation is complete under that plan and its round-124 corrections (see
the updates above).

## Jointly confirmed goal supplied by the user

Refactor the Kefu operational workflow so AI interprets user language, while
backend code exclusively validates business facts, performs state transitions,
and renders operational responses that accurately reflect committed database
state.

Proposed responsibility boundary to confirm or challenge:

- AI identifies intent and service type.
- AI extracts and normalizes user-provided fields.
- AI may semantically match or rank an arbitrary destination against the
  shared U-Choice address candidates supplied by the backend.
- AI returns candidate IDs and structured matched/ambiguous/unmatched evidence,
  not operational prose for delivery.
- The backend validates every AI-selected identifier against the supplied
  candidate set and owns ambiguity thresholds/confirmation policy.
- The U-Choice address book is shared by authorized U-Choice services; address
  visibility is not partitioned by `customer_id`.
- Outbound feasibility is calculated in total boxes across all compatible
  inventory buckets in the chosen warehouse. Requested pallet layout does not
  require a matching stored pallet bucket because boxes may be repalletized.
- The box-level rule applies both to the early feasibility decision and to
  atomic execution: source picks, leftovers, and final packing must conserve
  boxes, with stock revalidated when fulfillment is confirmed.
- The backend exclusively owns authorization, inventory arithmetic, request
  numbers, confirmation, cancellation, workflow transitions, persistence, and
  claims that an operation succeeded or completed.
- All user-visible operational replies are deterministic and derived from
  validated or committed backend results. AI-authored prose is not sent on an
  operational workflow path and AI must not claim a state change.
- Completion notices apply only to genuinely completed inbound/outbound
  requests, never read-only queries.

Clarifications, independently re-confirmed by a real Claude Code session in
round 114 (originally drafted in the mislabeled round 105):

- `customer_id` may remain as provenance/reporting metadata, but it does not
  control shared address visibility, reuse, or matching.
- “Reject early” starts after the warehouse is explicitly resolved or
  deterministically defaulted; no warehouse is guessed when policy leaves it
  unresolved.
- Existing Kefu single-transaction, replay/deduplication, and exactly-once
  completion-notice guarantees remain standing constraints.

Two non-blocking findings from round 114's review, to fold into
implementation: (1) Sec 6.3.4's stock-conflict clearing step should also
clear `destination_packing_lines`, not just `fulfillment_lines`/computed
picks; (2) plan Sec 12 transfers `core/kefu_turn_apply.py`/
`core/kefu_case_adapter.py` ownership from Claude Code (per the original
`kefu-migration-plan.md` Sec 12) to Codex for this phase -- a reasonable,
explicit transfer given Codex already built the confirm/cancel state machine
in those files (round 101), recorded here so it isn't a silent supersession.

## Motivating incident

A Kefu message requesting two 72-box pallets of S2 was correctly classified as
an outbound request. The reply nevertheless claimed an add-address transition
that the Kefu backend had not executed and prepended an unrelated successful
read-only request number as if a warehouse had completed it. The intended
behavior must also reject an outbound request when total available boxes are
insufficient before beginning address maintenance.

## Required sequence after the goal is signed

1. Codex and Claude Code independently confirm or challenge the candidate goal.
2. They write and sign a concrete implementation and regression-test plan.
3. The user explicitly authorizes implementation of that signed plan.
4. Only then may production files be changed.

Commit, push, deployment, operational API calls, and production database
changes remain separately authorized actions; none are inferred from approval
to discuss or plan.

## Relevant history

- Full chronological discussion: [`archive/discussion-rounds-001-102.md`](archive/discussion-rounds-001-102.md)
- Former detailed handoff: [`archive/status-through-round-102.md`](archive/status-through-round-102.md)
- Signed Kefu migration plan: [`kefu-migration-plan.md`](kefu-migration-plan.md)
- Current decision index: [`decisions.md`](decisions.md)
