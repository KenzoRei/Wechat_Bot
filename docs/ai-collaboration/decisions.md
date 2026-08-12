# Decision index

This is a navigation aid, not a replacement for signed plans or the
author-attributed discussion. A decision is marked **standing** only when it is
already signed/implemented or explicitly confirmed by the user. Items for the
new phase remain **candidate** until the next joint review.

## Standing architectural and process decisions

| Decision | State | Authority/history |
|---|---|---|
| Use a two-stage gate: jointly sign a plan, then obtain explicit user authorization before production implementation. | Standing | Collaboration charter and the completed Kefu migration process; rounds 77–79 in the archived discussion. |
| Kefu turn application uses an adapter-owned single transaction and must not call independently committing Smart Robot workflow helpers. | Standing, implemented | Rounds 91–98; independently audited again in round 102. |
| Duplicate turns and confirmations use the advisory-lock/`CaseExecution` mechanism and durable replay. | Standing, implemented | Rounds 93–101; round-102 audit. |
| Customer-facing copy is built from explicit allowlists rather than post-filtering internal text. | Standing, implemented | Round 101; round-102 audit. |
| Durable PDF regeneration must be byte-identical. | Standing, implemented | Round 101; round-102 audit. |
| Commit, push, deploy, callback registration, operational API calls, and production DB mutations require their own authorization where applicable. | Standing | Repeated handoff rule, including rounds 100–102. |
| `kefu-deterministic-response-plan.md` v2 (structured AI contract, deterministic outcome/renderer split, shared address matching, box-level feasibility, genuine Kefu-native pivot, completion-notice eligibility fix) is signed AND IMPLEMENTED, mutually cross-reviewed by both agents. Commit/push/deploy/production migration/operational API calls remain separately gated. | Standing, implemented | Signed: Codex rounds 104/106/108/110, real Claude Code round 114. Implemented: user authorization round 116, Claude Stage A/B round 118, Codex Stage C/D/E round 122. Cross-reviewed: rounds 119-121 (each side fixed a real bug the other found), Claude's final full-diff pass round 123 (no blockers, 2 minor non-blocking findings open). |

## Candidate decisions for the current phase

> **Round 112 correction, closed by round 114:** rounds 105, 107, 109, 111
> were authored by a Codex subagent mislabeled "Claude Code" (disclosed by
> Codex, see discussion.md round 112). Round 114 is this phase's first
> genuine independent Claude Code review: each item below was re-verified
> against the current repository from scratch and confirmed, not inherited
> from rounds 105-111.

These are now confirmed by a genuine independent Claude Code review (round
114) in addition to Codex's own review (rounds 105-106). Still not yet a
signed implementation contract for production code -- that's plan v2,
separately signed (see standing decisions above) -- and implementation
itself still requires the user's separate authorization:

| Candidate | State |
|---|---|
| AI is limited to semantic interpretation: service/intent selection, field extraction/normalization, and candidate-based semantic address matching/ranking. | Confirmed: Codex rounds 105-106, real Claude Code round 114 |
| Backend code owns business validation, state transitions, persistence, and all operational response rendering. | Confirmed: Codex rounds 105-106, real Claude Code round 114; verified against current code in round 114 (`kefu_turn_apply.py`/`kefu_case_adapter.py` do send AI prose today) |
| The U-Choice address book is shared across authorized U-Choice services and is not partitioned by `customer_id`. | User correction; confirmed: Codex rounds 105-106, real Claude Code round 114 |
| Stock feasibility and execution compare/conserve total boxes at the warehouse, allowing cross-pallet picking and repalletization. | User correction; confirmed: Codex rounds 105-106, real Claude Code round 114; verified against current code in round 114 (`_outbound_stock_error` checks one exact bucket today) |
| Read-only requests cannot produce warehouse-completion notices. | Confirmed: Codex rounds 105-106, real Claude Code round 114; verified against current code in round 114 (`kefu_completion_notice.py`'s query has no service-type restriction today) |

## Superseded or challenged assumptions

| Historical assumption | Current treatment | History |
|---|---|---|
| Kefu U-Choice addresses must be withheld until `customer_id` is locked and then filtered to that customer. | Challenged by the user: the address book is shared for U-Choice services. Do not treat the older rule as current for the new phase. | Implemented during round 99 under Kefu migration plan §6.2; reconsideration prompted by the live incident after round 102. |
| Outbound feasibility can be decided by matching requested pallet counts/configurations to stored pallet buckets. | Superseded by the user's box-level rule: compare total boxes and allow repalletization. | Current-phase conversation after round 102. |
| AI may announce that an unmatched address caused cancellation and a workflow pivot. | Rejected for future design unless the backend has actually committed the transition; wording must reflect real state. | Live incident after round 102. |
| Any successful Kefu request may be presented as warehouse-completed. | Rejected; read-only success is not warehouse completion. | Live incident involving `REQ-20260811-000720`. |
| `customer_id` attribution is required for Kefu `uchoice_inbound_request`/`uchoice_outbound_request` (plan Sec 5.5, signed by both agents this phase). | Rejected by the user: every current U-Choice service is performed on behalf of U-Choice itself, the sole platform tenant today. The 26-row `uchoice_customer` directory is delivery-*destination* companies, not tenants -- `customer_id`'s real role is closer to what `group_id` does for Smart Robot, meaningful only once Kefu serves a genuine second tenant. Removed from `kefu_turn_apply.py`'s readiness gate, `session_manager.py`'s candidate injection, and `kefu_customer_copy.py`'s render gate (round 124). `resolve_and_lock_customer`/`customer_candidates()` kept as dormant, tested infrastructure for a real future tenant. | User correction after round 123's final review. |

## Historical sources

- [`archive/discussion-rounds-001-102.md`](archive/discussion-rounds-001-102.md) — complete chronological, author-attributed record.
- [`archive/status-through-round-102.md`](archive/status-through-round-102.md) — former detailed dashboard/handoff.
- [`agreed-plan.md`](agreed-plan.md), [`systemic-validation-addendum.md`](systemic-validation-addendum.md), [`phase3-outbound-pdf-timing.md`](phase3-outbound-pdf-timing.md), and [`phase4-self-registration.md`](phase4-self-registration.md) — earlier signed phases.
- [`kefu-migration-plan.md`](kefu-migration-plan.md) — signed migration plan; portions explicitly flagged above are now candidates for revision, not silently rewritten.
