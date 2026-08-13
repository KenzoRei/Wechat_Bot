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
| `kefu-deterministic-response-plan.md` v2 (structured AI contract, deterministic outcome/renderer split, shared address matching, box-level feasibility, genuine Kefu-native pivot, completion-notice eligibility fix) is signed AND IMPLEMENTED, mutually cross-reviewed by both agents. Commit/push/deploy/production migration/operational API calls remain separately gated. | Standing, implemented | Signed: Codex rounds 104/106/108/110, real Claude Code round 114. Implemented: user authorization round 116, Claude Stage A/B round 118, Codex Stage C/D/E round 122. Cross-reviewed: rounds 119-121 (each side fixed a real bug the other found), Claude's final full-diff pass round 123 (no blockers, 2 minor non-blocking findings open, fixed round 124). Archived: `archive/discussion-rounds-103-124.md`. |
| AI is limited to semantic interpretation: service/intent selection, field extraction/normalization, and candidate-based semantic address matching/ranking. Backend code owns business validation, state transitions, persistence, and all operational response rendering. | Standing, implemented | Confirmed: Codex rounds 105-106, real Claude Code round 114. |
| The U-Choice address book is shared across authorized U-Choice services and is not partitioned by `customer_id`. | Standing, implemented | User correction; confirmed: Codex rounds 105-106, real Claude Code round 114. |
| Stock feasibility and execution compare/conserve total boxes at the warehouse, allowing cross-pallet picking and repalletization. | Standing, implemented | User correction; confirmed: Codex rounds 105-106, real Claude Code round 114. |
| Read-only requests cannot produce warehouse-completion notices. | Standing, implemented | Confirmed: Codex rounds 105-106, real Claude Code round 114. |
| `customer_id` is not required for `uchoice_inbound_request`/`uchoice_outbound_request` — every current U-Choice service is on behalf of U-Choice itself, the sole platform tenant. `resolve_and_lock_customer`/`customer_candidates()` kept as dormant infrastructure for a real future second tenant. | Standing, implemented | User correction after round 123's final review; implemented round 124. |

## Standing decisions from the Smart Robot / Kefu parity phase (rounds 125-129)

Implemented and mutually cross-reviewed. Only commit/push/deploy remains
separately gated (not yet authorized):

| Decision | State | Authority/history |
|---|---|---|
| `workflow_engine.py` needed a generic post-sanitization `input_schema.required` readiness predicate (mirroring Kefu's `_all_required_fields_present`) as the new authority at both readiness branch points, replacing `ai_response.all_fields_collected`, alongside the existing `auto_resolved`/`_outbound_required_fields_present` overrides (unchanged). Deletion-only (Claude Code's original round-125 scoping) would have stranded FedEx/UPS and most other services. | Standing, implemented | Codex's round-126 extension, independently verified round 127 (real DB: `fedex_label`/`ups_label`, 13 required fields each, both other overrides unconditionally false for them). Implemented by Claude Code, tested by both agents, reciprocally reviewed round 129. |
| `workflow_engine.py`'s `_handle_cancel` names the request's serial for an owned log only, reusing the function's existing `owns_log` boolean; a `targets_existing_request` session omits the serial (referenced, not owned log). | Standing, implemented | Signed rounds 126-127; implemented and tested round 128-129. |
| Session-conflict detection, the admin purge command, invoice-as-chat-file delivery, and `check_services` AI-vs-deterministic rendering are intentionally different per-channel and must NOT be ported to Smart Robot. | Standing, confirmed | Both agents, rounds 125-127. |
| `pre_confirm_validators.py` fixes, storage-listing grouping/zero-filtering, and the loose-pick outbound-completion resolver already apply to both channels (shared modules) — no action needed. | Standing, confirmed | Both agents, rounds 125-127; `pre_confirm_validators.py` verified to have zero `source_channel` references. |

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
- [`archive/discussion-rounds-103-124.md`](archive/discussion-rounds-103-124.md) — "deterministic Kefu operational responses" phase, complete/shipped.
- [`archive/status-through-round-124.md`](archive/status-through-round-124.md) — former detailed dashboard/handoff through round 124.
- [`agreed-plan.md`](agreed-plan.md), [`systemic-validation-addendum.md`](systemic-validation-addendum.md), [`phase3-outbound-pdf-timing.md`](phase3-outbound-pdf-timing.md), and [`phase4-self-registration.md`](phase4-self-registration.md) — earlier signed phases.
- [`kefu-migration-plan.md`](kefu-migration-plan.md), [`kefu-deterministic-response-plan.md`](kefu-deterministic-response-plan.md) — signed migration/response plans; portions explicitly flagged above are now candidates for revision, not silently rewritten.
