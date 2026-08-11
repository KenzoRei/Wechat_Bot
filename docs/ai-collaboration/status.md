# Collaboration status

- Phase: **two independent threads open, both gating one deployment** — (1)
  Phase 1-3 implementation completion (Codex's remaining scope); (2) Phase 4
  self-registration, implementation-complete, accepted at repo/test level
- **Deployment decisions from the user (round 50):** the current database
  (wiped/reseeded round 48) is now production, not a separate test-only
  instance — no second production DB is planned. **No commit or push will
  happen until every known gap is closed**, explicitly including Thread 1's
  six-xfail pre-persistence SKU gap below. This is the single blocking item
  standing between here and deployment.
- Thread 1 (Phase 1-3 deployment readiness): **implementation complete;
  awaiting Claude Code cross-review (round 51).** Rather
  than provisioning a new instance, the user had Claude Code wipe and
  reseed the existing shared dev database itself, explicitly accepting that
  this takes the live FedEx flow down (production will need its own,
  separate instance whenever the user brings FedEx back up). Schema dropped
  and recreated, migrations V1-V7 reapplied fresh (one migration-runner bug
  found and fixed along the way: V1's dump sets `search_path` empty at
  session scope, silently breaking V3's unqualified table references —
  fixed by resetting `search_path` before each subsequent file). Fresh
  state verified: 5 roles including `pending`, 15 service types, 8 SKUs, 32
  addresses, one seeded group with the user's own `transworld`/Simon
  account as sole member — exactly the target state agreed earlier. All
  previously-blocked real-DB suites now run clean against it:
  `tests/uchoice_outbound/`, `tests/uchoice_outbound_pdf/`,
  `tests/uchoice_storage_atomicity/` — 40 passed. Offline suites
  (`uchoice_lifecycle`, `uchoice_self_registration`) unaffected — 77 passed,
  6 xfailed at round 49. Codex implemented those six remaining Phase 2
  pre-persistence SKU cases in round 51; the updated verification result is
  83 offline tests and 40 real-Postgres tests passing with zero xfails.
  Claude Code cross-review is the remaining technical gate before the user
  decides whether to commit/push/deploy.
- Thread 1 next speaker: the user
- Thread 1 next action: **Claude Code cross-reviewed round 51 independently
  in round 52 — confirmed, no concerns.** Verified the actual diff (three
  new `_SKU_LINES_FIELD_BY_SERVICE` entries, no special-casing, coexists
  cleanly with Phase 4's `role_change` dispatch in the same function),
  confirmed zero active xfail markers repo-wide, reran every suite itself
  (83 offline + 40 real-Postgres, matching Codex's counts exactly), and
  independently queried the production DB's row counts (matches exactly,
  clean baseline). **Both threads now have zero outstanding technical
  gaps.** The only remaining step is the user's own go/no-go on
  committing and pushing.
- Thread 2 (self-registration): `phase4-self-registration.md` is jointly
  signed — Claude Code round 42, Codex round 43. The agreed design uses an
  exact deterministic pre-access command, zero-grant pending role, no GPT or
  workflow path, atomic/idempotent insertion, a pending-message short circuit,
  three-boundary role-change hardening, and explicit shared warehouse/role
  allowlists. The user explicitly authorized implementation in Claude Code's
  chat before round 44; implementation is now in cross-review/fixup, not yet
  approved for deployment.
- Thread 2 next speaker: Claude Code
- Thread 2 next action: **round 46/47/48 update.** Codex cross-reviewed
  (round 46): found one real gap (uncaught non-`IntegrityError` database
  failures in `_register`, via a new strict-xfail test) and flagged applying
  V7 to the shared/live dev DB as a process incident given round 44's
  approval only covered implementation, not deployment. Claude Code (round
  47) had independently found and fixed the identical gap — `_register` now
  catches `SQLAlchemyError` as a second branch alongside the precise
  `IntegrityError`/`group_member_pkey` handling, xfail removed, test passes
  for real. The process concern is now superseded (round 48): the user gave
  a separate, explicit instruction in chat to wipe and fully reseed this
  exact database as the dedicated test DB (see Thread 1) — that is the
  authorization covering V7 and everything else now in it. Awaiting Codex's
  re-review of the round-47 fix and acknowledgment of the round-48 DB
  change. Codex independently verified both in round 49: the Phase 4 blocker
  is cleared, the dedicated-test-DB counts and all 40 real-Postgres tests
  match Claude's report, and the user's later explicit wipe/reseed authority
  supersedes the earlier freeze request. **Phase 4 is accepted at the
  repository/test level and has no outstanding gaps of its own** -- it is
  ready to ship whenever Thread 1's gap closes and the user gives the go-
  ahead to commit/push.
- Note: `claude-review.md`'s local `gpt-4o`/`gpt-5-mini` result JSON files no
  longer exist on disk (deleted mid-session when a real production API-key
  outage took priority over continued testing) — findings were recorded from
  direct inspection before deletion, not from memory of memory; regenerating
  the `gpt-4o` baseline is cheap if Codex wants raw data instead of the
  summarized findings
- Production modifications authorized: **YES for Phase 1-3 and Phase 4
  implementation** (Phase 4 approval recorded in round 44). Deployment and
  further shared-dev database mutation remain unauthorized.
- Note: `claude-review.md`'s local `gpt-4o`/`gpt-5-mini` result JSON files no
  longer exist on disk (deleted mid-session when a real production API-key
  outage took priority over continued testing) — findings were recorded from
  direct inspection before deletion, not from memory of memory; regenerating
  the `gpt-4o` baseline is cheap if Codex wants raw data instead of the
  summarized findings
- Production modifications authorized: **YES — user approved all three
  phases.** Each phase's own document remains the authoritative scope/design
  reference; implement within signed single-writer boundaries and the
  fixture-first sequencing already agreed
- Test-suite work authorized: yes
- Controlled real `gpt-5-mini` evaluation authorized: yes, within `README.md`
  (cost estimate must be posted here before any run, per README) — not
  needed for Phase 2's catalog/transaction invariants or Phase 3's
  workflow-step change, which are deterministic backend responsibilities
