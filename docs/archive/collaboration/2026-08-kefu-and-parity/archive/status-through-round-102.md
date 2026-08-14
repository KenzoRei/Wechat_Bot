# Collaboration status

> **CODE HANDOFF COMPLETE, INDEPENDENTLY AUDITED (round 102):** Codex
> resumed Claude Code's round 100 handoff and completed all three remaining
> code items locally: the Kefu-native confirm/cancel state machine with a
> guarded logical confirmation claim, allowlist-built customer-copy
> rendering, and durable outbound PDF/file delivery plus byte-identical
> regeneration/replay. Claude Code then independently cross-reviewed round
> 101 (re-ran every test suite itself, read the diffs line by line,
> hand-traced the concurrency logic) and confirmed the claims: **221
> passing tests** (180 offline + 41 real Postgres), no correctness
> blockers. Two minor, non-blocking follow-ups remain open (see round 102
> in discussion.md): a dedicated regression test for the terminal-case
> multi-staff binding clear, and a stale comment referencing the old
> `_READ_ONLY_KEFU_SERVICES` name. No operational API call, migration,
> commit, push, callback registration, or deployment occurred. The only
> remaining rollout input is the user's confirmation of
> `config.SERVER_BASE_URL` as the live public domain.

- **Thread: Smart Robot → WeChat Kefu migration.** Plan fully signed
  (`kefu-migration-plan.md` v7, Claude Code round 77 / Codex round 78).
  **Round 79 — the user authorized implementation.** Claude Code is now
  building its portion per §12's single-writer split (all migrations/
  models §2, customer/address backfill, staff auth + tagged `role_change`
  dispatch §2.3, session/case/turn-audit/execution-ledger §2.5, warehouse
  defaults §3, notifications §7, PDF artifact refactor §4). Codex's
  portion (crypto/XML adapter, Kefu API client, callback/sync worker,
  reply/file transport §5/§6.1/§11.3) is untouched — separate ownership,
  implemented independently.
- **Round 80 — Claude Code's Sec 2 data model complete.** `V8__kefu_data_model
  .sql` applied to production; all 8 new tables verified present and empty,
  all pre-existing data untouched; new/updated models
  (`models/kefu.py` + session/request_log/interaction_log/uchoice); a real
  pre-existing model-import-ordering bug (empty `models/__init__.py`) caught
  and fixed along the way. All 40 real-Postgres + 65 offline tests pass.
  Remaining Claude Code work: customer/address backfill, staff auth + tagged
  role_change dispatch, warehouse defaults + `get_original_fields()` fix,
  PDF artifact refactor, notifications/scheduler filtering.
- **Round 81 — Codex transport scope complete offline.** Crypto/XML, Kefu API
  client, isolated callback router, serialized sync ingestion/leased worker,
  and durable reply/file delivery are implemented. All 27 new Kefu tests plus
  65 existing offline regression tests pass (92 total). No operational call,
  DB mutation, callback registration, commit, push, or deployment occurred.
  Integration awaits Claude's case-turn/artifact interfaces and cross-review.
- **Round 82 — Claude Code cross-reviewed round 81, confirmed, no
  concerns.** Verified the claim-query implementation matches the signed
  design exactly, contracts match §11.1, all 92 tests pass independently,
  no Claude-owned files touched. Continuing Claude Code's remaining
  implementation now; integration wiring follows once that's done.
- **Round 83 — customer/address backfill complete.** 26 `uchoice_customer`
  rows backfilled from existing addresses; `address_candidates()`/
  `UpsertAddressHandler` customer-scoped.
- **Round 84 — staff authorization + tagged role_change dispatch
  complete.** `check_kefu_access()`, `core/role_identity.py`'s tagged
  target-identity contract wired into all three `role_change` boundaries,
  cross-channel last-admin-protection fix, `member_candidates()` merged
  view. 142 tests passing.
- **Round 85 — warehouse defaults for inbound + `get_original_fields()`
  fix complete.** `V9__inbound_warehouse_optional.sql` applied;
  `_resolve_inbound_warehouse_default()` mirrors the outbound resolver;
  `get_original_fields()` now resolves via `origin_session_id` FK
  (channel-agnostic). Found and fixed a real bug along the way: the new
  FK broke 4 test fixtures' delete order, causing cascading stray-session
  test failures — fixed, verified 142/142 passing.
- **Round 86 — PDF handler artifact refactor complete.**
  `handlers/uchoice/pdf_stub.py` now produces a channel-neutral
  `Artifact` (bytes/filename/content_type/artifact_key); Smart Robot
  wraps it in a download token exactly as before via a new, separate
  `_wrap_artifact_for_smart_robot()` — Kefu's transport will call the
  builder directly instead once wired in.
- **Round 87 — notifications complete. Claude Code's full §12
  implementation scope is now finished.** Scheduled jobs filtered to
  `source_channel='smart_robot'`; `SMART_ROBOT_ENABLED`/`KEFU_ENABLED`
  feature flags added to `config.py` (§6.4); new on-demand
  `view_pending_digest` service (`V10` migration). Deferred:
  `completion_notice_shown_at` audience tracking, which depends on Kefu's
  live case-turn flow — left for the integration-wiring phase.
- **Round 88 — Codex verified rounds 83–87 and is integration-ready.** Two
  Codex-owned seams were aligned to §11: explicit deterministic
  `case_number_hint` + complete result variants, and validated coercion of
  Claude's dictionary Artifact into Codex's delivery representation. 109
  offline tests pass. Integration must also cover: Kefu registration and the
  fixed account-to-group mapping; channel-neutral final workflow rendering;
  genuinely Kefu-only startup without Smart Robot credentials; and deferred
  completion-notice audience tracking.
- **Round 89 — Claude Code implemented all four round-88 requirements plus
  the case-turn adapter (`core/kefu_case_adapter.py`) and application
  wiring (`main.py`, feature-gated).** Real replies now go through
  `kefu_delivery.enqueue_text` + a new scheduled delivery sweep
  (`core/kefu_delivery_worker.py`), giving Sec 11.3's closed-window/retry
  handling instead of a direct send. 188 tests pass (148 offline + 40
  real-Postgres). Found and fixed a related Kefu-only-startup bug along
  the way (`WECHAT_CORP_ID` was wrongly gated to Smart Robot only —
  Kefu's own client needs it too). **Two open items need Codex's read,
  not settled unilaterally:** (1) `case_turn`'s audit write happens in a
  separate transaction from the business mutation inside
  `workflow_engine.run_and_get_reply`, which narrows but doesn't close
  round-70 finding 1's duplicate-msgid replay guarantee — a crash between
  the two loses the audit row without losing the mutation; (2)
  `WXBizXmlMsgCrypt`'s `receive_id` was set to `WECHAT_CORP_ID` per
  WeChat's documented Kefu contract, unverified against the live API.
  **Explicitly deferred, not silently dropped:** Sec 6.3 customer-copy
  rendering, `customer_id` collection for Kefu-originated
  inbound/outbound requests (Sec 6.2), and PDF/file delivery end-to-end
  (`core/kefu_artifact_loader.py` is a deliberate `NotImplementedError`
  stub — text delivery is fully wired, file delivery is not). Full
  detail in discussion.md round 89.
- **Round 90 — Codex reviewed round 89 and found six rollout blockers.**
  The 148 offline tests pass, but the untested `_process_turn` path currently
  rolls back its durable reply enqueue; duplicate-msgid replay reads an empty
  reply from the msgid-bearing user row; the signed CAS/execution-ledger
  exactly-once design is not wired; Kefu request provenance defaults to
  `smart_robot`; explicit/bound cases are not reauthorized per turn; and
  completion-notice claiming is racy and marks shown before durable delivery.
  Codex confirmed CorpID is the correct XML `receive_id` design, subject to the
  final live GET verification before callback registration. Atomicity must be
  closed before mutating-service rollout. Customer selection/locking,
  customer-copy rendering, and PDF/file delivery should be completed next (or
  their dependent Kefu services explicitly disabled), not silently deferred.
- **Round 91 — Claude Code fixed five round-90 blockers and mitigated the
  sixth with a read-only rollout gate, verified
  against real Postgres (199 tests: 156 offline + 43 real-Postgres,
  including 3 new integration tests specifically proving what a mock
  can't: durable-enqueue survival, replay-payload location, and
  concurrent notice-claim safety).** Blocker 3 (CAS/exactly-once) was
  not implemented unilaterally — per Codex's own stated fallback,
  `core/kefu_case_adapter.py` now restricts Kefu case processing to
  read-only services (`_READ_ONLY_KEFU_SERVICES`) until a joint design
  for the real CAS/execution-ledger work is agreed. The three deferred
  product requirements (customer_id selection, customer-copy rendering,
  PDF/file delivery) are explicitly scoped as next, not dropped again —
  proposing to start with customer_id selection/locking since it's the
  actual blocker for `uchoice_inbound_request`/`uchoice_outbound_request`
  specifically. Full detail in discussion.md round 91.
- **Round 92 — Codex verified round 91's component fixes; 156 offline tests
  pass.** The read-only gate prevents duplicate business mutations but does
  not close crash replay: `workflow_engine` can still commit session/log state
  before `_finalize_turn`, leaving no `case_turn` replay row and potentially
  causing an orphan/duplicate session or a unique-msgid failure. The new DB
  tests exercise `_finalize_turn`, not the full processor. Codex therefore
  requires the signed conversational CAS + `case_execution` recovery design
  and full `_process_turn` failure-injection tests next. Agreed order: CAS/replay
  foundation, customer selection/locking, customer-copy rendering, then PDF/
  file delivery and artifact replay. Inbound/outbound remain disabled until
  both CAS and customer locking are complete.
- **Round 93 — Claude Code implemented the CAS + execution-ledger design
  for the read-only slice, verified through the real orchestration path
  (201 tests: 156 offline + 45 real-Postgres).** `case_execution` now
  wraps the whole turn per the signed states; a new, additive
  `workflow_engine.py` hook sets `db_committed_at` in the SAME commit as
  `request_log` creation (V12 migration made `case_execution.session_id`
  nullable, since a brand-new case has no session yet at claim time).
  The named crash-then-retry gap is closed for read-only services, with
  a new `tests/kefu_integration/test_kefu_process_turn_crash_recovery.py`
  proving it through `make_case_turn_processor()` itself, not an
  isolated helper. Honestly scoped: mutating-service ledger wiring is
  NOT done (inbound/outbound remain gated off), recovery uses a generic
  status reply (the original AI text is genuinely unrecoverable after a
  real crash, not byte-identical replay), and only 2 of Codex's 5 named
  failure-injection boundaries were tested (the 2 reachable in today's
  read-only-only scope). Full detail in discussion.md round 93.
- **Round 94 — Codex verified the new full-processor tests and reran 156
  offline tests, but found four remaining foundation blockers.** The claimed
  same-transaction ledger transition is actually separated from
  `request_log` creation by `create_log()`'s internal commit; the injected
  crash occurs after both commits and misses that gap. The revision CAS runs
  only after independently committed conversation/session writes, so a losing
  turn can leave durable state. Existing execution rows are returned without
  any status/lease ownership decision, and recovery infers from
  `request_log` rather than guarded ledger states. Mutating services remain
  gated. Claude should repair the transaction/claim state machine and add the
  precise inter-commit, overlapping-msgid, and cross-staff CAS tests before
  customer selection becomes the primary implementation track.
- **Round 95 — Claude Code rebuilt the read-only allowlist's turn
  processing around a genuine single-transaction boundary, addressing
  all four round-94 findings (203 tests: 156 offline + 47 real-Postgres).**
  New `core/kefu_turn_apply.py` builds a turn's business state via
  `db.add()`/`db.flush()` only, bypassing `workflow_engine.py`'s
  independently-committing helpers entirely for this path;
  `core/kefu_case_adapter.py`'s `_process_turn` commits exactly once,
  bundling business state + execution-ledger completion + case_turn audit
  + delivery enqueue together. A crash before that commit now leaves
  nothing durable at all, including the execution-ledger row itself. New
  `tests/kefu_integration/` covers all three of Codex's explicit asks:
  the exact crash boundary, a real two-thread overlapping-msgid test, and
  a real two-thread cross-staff revision-CAS race test — all stable
  across 3 repeated runs. One honest note flagged: the execution claim's
  `"in_progress"` branch is currently unreachable in normal operation
  (the advisory lock blocks rather than failing fast), left in as
  defensive code, not silently removed. Mutating services remain gated
  off — proposing to wire them next using this now-proven pattern, then
  customer selection. Full detail in discussion.md round 95.
- **Round 96 — Codex accepts round 95's single-transaction architecture as
  the read-only foundation; 156 offline tests pass.** Two orchestration seams
  remain: a concurrent duplicate that waits for the advisory lock sees the
  completed execution but raises instead of re-querying and replaying the
  stored `CaseTurn`; and `kefu_turn_apply` does not append continuation user
  messages or final assistant replies to `conversation_history`. The
  overlapping-msgid test currently permits the first error, and the
  cross-staff CAS assertion has a `+1` tolerance that can hide loser residue.
  Claude should make these narrow fixes and tighten the tests, then move to
  customer selection/case-level customer locking while designing mutating
  recovery alongside it. Inbound/outbound remain gated until both are done.
- **Round 97 — Claude Code fixed both round-96 seams, verified real-Postgres
  (204 tests: 156 offline + 8 real-Postgres).** Replay lookup now runs after
  acquiring the advisory lock (wait-then-replay is real); `kefu_turn_apply`
  now appends the continuation user message and the final/question assistant
  reply exactly once per turn, mirroring `workflow_engine`. Fixing the
  history append surfaced a real latent bug: `_finalize_turn`'s post-CAS
  `db.refresh(session)` was silently discarding every OTHER pending,
  unflushed attribute on the session (`status`, `collected_fields`, now
  `conversation_history`) because `SessionLocal` is `autoflush=False` — fixed
  by setting the two known-changed CAS columns directly instead of blindly
  refreshing. Cross-staff CAS test tightened to exact-equality assertions.
  Full detail in discussion.md round 97.
- **Round 98 — Codex accepted round 97 and the read-only CAS/replay
  foundation; 156 offline tests pass.** The post-lock replay and ordered
  continuation-history fixes are correct, and Codex confirmed that the old
  whole-row `refresh()` could discard pending session state under
  `autoflush=False`. The cross-staff test now has exact audit/delivery/revision
  counts; customer-selection concurrency tests should additionally use
  distinguishable field/history/customer values to directly prove loser
  residue cannot survive. Claude may proceed to deterministic customer
  selection and case-level `customer_id` locking, designing mutating recovery
  alongside it. Inbound/outbound remain gated through the remaining product
  and recovery requirements.
- **Round 99 — Claude Code built and tested customer selection/locking
  (216 tests: 172 offline + 10 real-Postgres), and surfaced a real scope
  finding while trying to test it end-to-end.** New
  `core/uchoice_customer.py` (`resolve_and_lock_customer`), candidate
  injection/withholding wired into `core/session_manager.py`, AI prompt
  contract added, and `core/kefu_turn_apply.py`'s readiness gate now
  requires a resolved `customer_id` in code for the three customer-scoped
  services. While building the requested distinguishable-values concurrency
  test, found that `uchoice_inbound_request`/`uchoice_outbound_request`/
  `upsert_address` all have `requires_confirmation=True` in the real
  catalog, but `apply_kefu_turn` has no confirm/cancel handling at all (it
  was built for the read-only allowlist, where nothing requires
  confirmation) -- so none of the three can actually run through it yet,
  and a full pipeline concurrency test for them would be misleading rather
  than proving anything. Proposing the Kefu-native confirm/cancel state
  machine (the actual "mutating-service ledger path") as the concrete next
  step, since customer-locking depends on it to be end-to-end testable.
  `_READ_ONLY_KEFU_SERVICES` is untouched -- nothing customer-scoped is
  reachable through the live rollout gate. Full detail in discussion.md
  round 99.
- **Round 100 — Claude Code: session handoff notice.** The session ended on
  context/token limit. Full detail in discussion.md round 100 (state, exact
  file-level pending work, why item 1 blocks everything customer-scoped).
  Standing rule reaffirmed: no commit/push/deploy without the user's own,
  separate, explicit say-so.
- **Round 101 — Codex resumed Claude Code's round-100 handoff and completed
  all three remaining code items (221 tests: 180 offline + 41 real-Postgres).**
  Kefu-native confirm/cancel state machine (`core/kefu_turn_apply.py`) with a
  guarded logical confirmation claim (`kefu-confirm:{session_id}:{revision}`
  under the existing advisory-lock mechanism); allowlist-built customer-copy
  rendering (`core/kefu_customer_copy.py`); durable outbound PDF/file
  delivery with byte-identical regeneration/replay (`core/kefu_artifact_
  loader.py`, `reportlab` determinism fix). `_KEFU_ENABLED_SERVICES` (renamed
  from `_READ_ONLY_KEFU_SERVICES`) now includes `uchoice_inbound_request`/
  `uchoice_outbound_request`/`upsert_address`. No commit, push, deploy,
  callback registration, migration, or operational API call occurred. Full
  detail in discussion.md round 101.
- **Round 102 — Claude Code independently cross-reviewed round 101; verdict:
  correct, no correctness blockers.** Re-ran all 221 tests directly, read
  the diffs line by line, hand-traced the confirmation-race concurrency
  logic, and confirmed the `db.refresh()` call in the new confirm path
  doesn't repeat round 97's autoflush bug. Verified Codex stayed inside its
  claimed file scope. Two minor, non-blocking follow-ups flagged: (1) no
  dedicated regression test for the terminal-case multi-staff binding
  clear; (2) a stale `_READ_ONLY_KEFU_SERVICES` comment in
  `tests/kefu_integration/test_kefu_process_turn_crash_recovery.py`'s
  docstring. Full detail in discussion.md round 102.
- Next speaker: **Whoever the user directs to pick this up next (fresh
  Claude Code session or Codex) — fold in round 102's two minor follow-ups
  (dedicated terminal-clear test, stale comment fix), then the only
  remaining rollout input is the user confirming `config.SERVER_BASE_URL`
  before any real Kefu callback can be registered.**
- Outstanding item, unrelated to implementation start: `config
  .SERVER_BASE_URL` still not confirmed by the user as the correct live
  Render domain (open since round 56) — needed before any Kefu callback
  URL is registered with WeChat, not before code is written.
- Prior work (Phases 1-4, self-registration) is fully shipped — commit
  `7374037` on `main`. Unrelated in scope to this thread.
- Production modifications for this thread: **AUTHORIZED (round 79)** —
  within each agent's own signed single-writer boundary. No commit/push/
  deploy without the user's separate say-so.
- Note: `claude-review.md`'s local `gpt-4o`/`gpt-5-mini` result JSON files
  no longer exist on disk (deleted mid-session, findings recorded from
  direct inspection before deletion) — unrelated to this thread, kept for
  continuity.
