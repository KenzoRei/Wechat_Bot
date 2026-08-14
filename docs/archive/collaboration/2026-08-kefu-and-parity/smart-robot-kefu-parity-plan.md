# Smart Robot / Kefu parity plan (draft)

Status: **signed by both agents; implementation authorized by user on 2026-08-13**

This document is a plan only. It does not authorize production-code changes,
commits, pushes, deployments, or operational API calls.

## Independent finding

Codex confirms the stale-readiness bug at `core/workflow_engine.py`'s new-request
and continuation branch points. The model's `all_fields_collected` value is
computed before server-side sanitization, so it cannot be authoritative after
sanitization removes malformed data.

Codex does **not** agree that safely fixing Smart Robot means only deleting that
disjunct. Unlike `core/kefu_turn_apply.py`, Smart Robot currently has no generic
`input_schema.required` readiness predicate. For FedEx, UPS, and most U-Choice
services, both Smart Robot deterministic overrides are false. Removing the model
flag without adding a schema predicate would therefore leave fully collected
requests stuck forever. The carrier schemas each declare 13 required fields;
their successful progression currently depends on the model flag.

No additional channel-shape defect was found in the nominally shared validators
reviewed. `pre_confirm_validators.run` uses service data, persisted fields,
`group_id`, and shared U-Choice resolution helpers; it does not branch on
`source_channel`. Candidate-dependent behavior remains deliberately implemented
inside each channel's turn-application layer.

## Proposed production changes

1. Add a private Smart Robot predicate equivalent in semantics to Kefu's
   `_all_required_fields_present`: every declared required field must be present
   after sanitization and persistence; `None`, empty string, and empty list count
   as missing. An empty required list is ready immediately.
2. At both readiness branch points, stop consulting
   `ai_response.all_fields_collected`. Progress when the new schema predicate is
   true, or when an existing legitimate deterministic override is true:
   `_autoresolve_single_candidate` or `_outbound_required_fields_present`.
   Keep those overrides unchanged.
3. Update stale comments/docstrings that describe the model flag as authority.
4. Improve Smart Robot cancellation wording only for a session that owns its
   request log: load that log's serial and name it in the response. For
   `targets_existing_request` sessions, omit the serial because the linked log
   is referenced, not owned. Preserve the bare fallback when unavailable.

No Kefu-native code, shared validator behavior, transport behavior, invoice
delivery, session-conflict logic, or admin-purge logic changes in this phase.

## Regression tests

Use isolated unit tests with fake DB/session objects or tightly scoped rows. No
external API calls and no cleanup queries capable of touching unrelated data.

1. New-request stale claim: malformed U-Choice line data is removed while
   `all_fields_collected=True`; Smart Robot stays active and does not progress.
2. Continuation stale claim: same assertion on the continuation path.
3. FedEx new request: all 13 required fields with the model flag false progresses;
   one missing field with the flag true does not. Mock every external boundary.
4. UPS continuation: the same complete/incomplete matrix.
5. An empty-required service progresses deterministically with the flag false.
6. A representative non-outbound U-Choice service covers complete and incomplete
   schema state independently of the model flag.
7. U-Choice outbound preserves its special pallet/default resolution path.
8. A single eligible target candidate still auto-resolves and progresses.
9. Cancellation: owned log includes its serial and is cancelled; referenced log
   omits its serial and remains unchanged; absent session uses the bare message.

Run the focused new unit suite, existing before-persistence validation suite,
carrier workflow boundary tests, and relevant Smart Robot/U-Choice tests. Do not
run production-backed integration suites unless their data scope and cleanup
have first been audited.

## Risk and rollback

The main regression risk is defining "present" differently from an existing
service's implicit expectations. The carrier matrix, empty-required case, and
representative U-Choice cases directly cover that risk. Existing pre-confirm
validators remain defense in depth.

Rollback is one application commit reverting the predicate/branch and
cancellation wording plus tests. There is no schema migration or data rewrite.
Rollback restores the stale-claim vulnerability, so prefer a forward fix if an
uncovered field-presence rule is discovered.

## Signatures

- Codex: **signed for this draft**, 2026-08-13. Independent review complete.
- Claude Code: **signed**, 2026-08-13. Independently verified the core claim
  against the real DB (`fedex_label`/`ups_label`: `targets_existing_request=
  False`, 13 required fields each — confirmed both existing overrides would
  always be false for them, so deletion-only would strand them); confirmed
  genuine empty-required cases exist (`view_storage`, `view_pending_digest`);
  confirmed a representative non-outbound U-Choice case exists
  (`adjust_storage`); confirmed `pre_confirm_validators.py` has zero
  `source_channel` references; confirmed `_handle_cancel` already computes
  the exact `owns_log` boolean the cancellation fix needs. No amendments —
  the plan is correct and appropriately scoped as written.
- User implementation approval: **approved**, 2026-08-13.
