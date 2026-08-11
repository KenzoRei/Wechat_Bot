# Phase 4: self-registration into a zero-permission `pending` role

Status: **jointly signed — Claude Code round 42; Codex round 43.** Not yet
approved by the user. No
production, migration, or test file has been touched for this feature. This
document exists because Codex's round-35 audit (§4 of that round)
recommended the feature get its own signed phase document, matching the
process already used for Phase 1/2/3, given it opens a new
pre-authentication write path and a new authorization state.

## 1. Problem

Today there is no in-chat way to add a new member to a group. The only path
is the admin HTTP API (`api/admin/members.py`), which requires an
out-of-band `wechat_openid` the admin has no way to look up from inside
WeCom — the Smart Robot webhook payload only ever exposes the *sender's own*
`from.userid`, confirmed by direct read of `core/webhook_receiver.py` earlier
in this session. A brand-new group therefore has no way to onboard anyone
beyond whoever was manually seeded.

## 2. Why this can't be an ordinary granted service

Confirmed by Codex (round 35) via direct read of `api/webhook.py:117-127`:
`check_access` runs before context is built or the AI/workflow engine is
invoked. A sender with no `group_member` row gets `AccessDenied` and the
pipeline returns immediately. An ordinary `register_member` service —
however it's granted — is therefore unreachable by the exact population that
needs it: unregistered senders never get an `allowed_services` list to find
it in.

## 3. Design (Codex's round-35 proposal, adopted in full)

Registration is a **deterministic pre-access system command**, not an
AI-routed business service, and does not participate in the normal
service/workflow/confirmation machinery at all.

1. In `api/webhook.py`'s `_process_message`, after the message is decrypted
   and **before `access_control.check_access` is called**, recognize only an
   **exact, normalized command** (e.g. `注册成员`) — not a fuzzy keyword
   match. Chosen deliberately to avoid accidental triggers from ordinary
   conversation, and placed before `check_access` specifically because the
   population this feature exists for — a sender with no `group_member`
   row — gets `AccessDenied(reason="user_not_member")` from `check_access`
   and would never reach anything gated behind a successful access
   resolution (see round-39 correction below).
2. Required preconditions, all checked deterministically by the
   registration branch's **own** group/member lookup — not derived from
   `check_access`, which this branch runs ahead of: `chat_type == "group"`,
   a non-empty `message["from_user"]`, the group resolves to an existing
   active `group_config` row, and the message content matches the exact
   command. The identity written is always `message["from_user"]` — never
   anything parsed from the message body, so there is no vector for
   registering an arbitrary third party. This same branch's own membership
   lookup is also what produces the "already registered" vs. "already
   pending" distinction in point 9 below — an already-operational member
   who sends the command is caught here too, not by `check_access`.
3. Resolve the `pending` role (new seed row in the `role` table, zero
   `group_service_role` grants — no schema change, reuses the existing
   role-grant mechanism as-is) and insert `(wechat_openid, group_id,
   role_id=pending)` into `group_member` in one transaction. Confirmed via
   direct read of `models/group.py:23-33`: `group_member`'s primary key is
   the composite `(wechat_openid, group_id)`, so a duplicate-registration
   race is caught by the database itself, not by an application-level
   check-then-insert race window.
4. No `conversation_session`, no `request_log`, no AI call. Registration is
   inert: it grants no operational service, needs no confirmation, and
   costs nothing beyond one insert.
5. **Corrected per Codex round-39 finding 1** — the round-38 wording placed
   this same exact-command check "immediately after access resolution
   succeeds," which directly contradicted point 1 above and, worse, is
   impossible for the sender this feature serves: an unregistered sender's
   `check_access` call returns `AccessDenied`, never "succeeds." There is
   exactly **one** registration-command branch, and it runs before
   `check_access` (point 1), using its own membership lookup (point 2) —
   there is no second, post-access copy of this check. What *does* run
   after access resolution is the distinct pending-member short circuit in
   point 7 below, which only applies to *non-command* messages from a
   sender who already has a `group_member` row with `role=pending`.
6. `register_member` does **not** get a `PRE_CONFIRM_VALIDATORS` entry or a
   before-persistence sanitizer — there is no AI output and no
   `collected_fields` to trust here. Its own deterministic function
   re-checks group/activity, duplicate membership, sender identity, and the
   `pending` role's existence at the point of the write. If it's also
   represented in `service_type` purely for admin-visibility/documentation
   purposes, that catalog row must not be reachable through the normal
   role-grant execution path — the pre-access command is the only real
   entry point.
7. **Pending-role short circuit, added per round-35 finding 1, placement
   corrected per round-39**: this is the branch that actually runs *after*
   access resolution — distinct from point 1's pre-access command branch.
   Once `check_access` returns an `AccessResult` (i.e. the sender does have
   a `group_member` row) and that member's role is `pending`, and the
   message is **not** the exact registration command (already handled by
   point 1 regardless of role), intercept with a fixed "registration
   received, awaiting an administrator" reply **before**
   `resolve_session`, `build_context`, or `ai_chain.process`. Round-35
   confirmed an empty `allowed_services` list doesn't crash
   `access_control`, `session_manager._build_uchoice_candidates`, or
   `prompt_builder` (they all safely iterate `[]`), but without this
   short-circuit the pipeline would still construct a session/context and
   pay for a full GPT call on every message from a member who can do
   nothing with the result. This keeps the zero-permission role genuinely
   inert, not just harmless.
8. **Command normalization, defined precisely per Codex round-37 §3**: after
   the existing bot-mention removal already performed upstream, strip
   surrounding whitespace, apply Unicode NFKC normalization, then compare
   for **exact equality** against one canonical command string. Substrings,
   trailing/leading prose, and fuzzy variants must not match. Canonical
   command text, accepted by both agents: `注册成员`.
9. **Precise duplicate/error semantics, per Codex round-37 §3** — three
   distinct outcomes, not one collapsed "already registered" message:
   - A retry of the exact command by a sender who is *already* `pending` in
     this group, and a simulated concurrent duplicate-insert race, share
     one response: "already registered; awaiting administrator
     assignment."
   - A sender who is already an operational (non-`pending`) member of this
     group gets a distinct response: "already registered" (no
     "awaiting assignment" language — they're not awaiting anything).
   - **Named precisely per Codex round-39 finding 3**: only the specific
     composite-primary-key violation on `group_member`'s `(wechat_openid,
     group_id)` PK is caught and mapped to the duplicate-response path
     above — identified concretely, for PostgreSQL, by
     `IntegrityError.orig.diag.constraint_name == "group_member_pkey"`
     (with a defensive equivalent check if the SQLAlchemy driver wrapper in
     use exposes this differently), not by treating every `IntegrityError`
     as a duplicate. Any other failure (the `pending` role row missing, an
     FK violation, any other database error, or an integrity error against
     a *different* constraint) is **not** reinterpreted as a duplicate —
     roll back the transaction, log the real failure, and return one
     generic "registration failed, please try again" response. Silently
     mapping unrelated failures to "you're already registered" would hide
     real faults behind a false-success message.

## 4. `role_change` hardening: three-boundary validation, not pre-confirm alone

Found by Codex during the round-35 audit (its answer to audit question 3),
independently verified here by direct read of
`handlers/uchoice/role_change.py:17-34`:

```python
new_role_name = fields.get("new_role")
warehouse_code = fields.get("warehouse_code")
...
member.warehouse_code = warehouse_code if new_role_name == "warehouseman" else None
```

`warehouse_code` is *cleared* on any non-warehouseman role, but nothing
rejects `new_role_name == "warehouseman"` with `warehouse_code` missing or
blank — the requirement that a warehouseman needs a `warehouse_code` exists
only as a prompt hint to the AI today, confirmed absent from
`core/pre_confirm_validators.py` (`role_change`'s only current entry is
`_last_admin_protection`, `core/pre_confirm_validators.py:399`). An admin
promoting a newly-`pending` member straight to `warehouseman` — the exact
new workflow this phase introduces — can silently create a warehouseman
with `warehouse_code=None`, which then breaks warehouse-scoped completion
checks in `handlers/uchoice/lookup_validate.py`.

**This phase's scope includes fixing it**, since Phase 4's new promotion
path (pending → warehouseman) is what would trigger the bug in practice.
Codex's round-37 review (§2) pointed out that a pre-confirm-only fix is
insufficient: it still allows a fabricated `target_openid`/`new_role` into
persisted `collected_fields`, and pre-confirm itself can be bypassed by
stale state or direct execution outside the normal AI-driven turn. `target_
openid` is a candidate-backed identifier exactly like the SKU codes Phase
1/2 already protect, so it gets the **same three-boundary pattern** already
signed for those:

1. **Before persistence** (extending `_sanitize_extracted_fields_before_
   persistence`, `core/workflow_engine.py`, the same function Phase 1/2
   already use for SKU-line sanitization): accept `target_openid` only if
   it names a current `group_member` of this group; accept `new_role` only
   if it is one of the allowed assignable roles (§5 below). Invalid values
   are omitted, not silently kept — this must not block unrelated valid
   fields already collected in the same turn, matching the existing
   omit-the-invalid-field behavior used for malformed SKU lines.
2. **Pre-confirm** (extend `role_change`'s `PRE_CONFIRM_VALIDATORS` entry,
   composed alongside `_last_admin_protection` via the existing
   `_compose(...)` pattern, `core/pre_confirm_validators.py:398-418`):
   recheck target membership, recheck `new_role` is assignable, and require
   a valid non-blank `warehouse_code` when `new_role == "warehouseman"`.
3. **Execution backstop** (inside `RoleChangeHandler` itself,
   `handlers/uchoice/role_change.py`): repeat the same authoritative checks
   — target is a current member, role is assignable, warehouse_code is
   present, non-blank, and validated against
   `VALID_WAREHOUSE_CODES` — immediately before the mutation. The handler
   must fail controlled (a clean `RuntimeError`, per this codebase's
   existing handler-error contract) if any invariant doesn't hold, so it's
   safe even if invoked outside the normal confirm-turn path, not just
   relying on the two upstream layers.

**Warehouse-constant home, corrected per Codex round-41 finding 1**: the
round-40 revision pointed this check at `jobs.uchoice_daily.WAREHOUSES`
directly, which Codex flagged as a bad dependency direction — core
validation code should not import a scheduled-job module, especially one
with a bound WeChat client (`clients.wechat_client.send_group_webhook_message`,
`jobs/uchoice_daily.py:25`). Confirmed by repository search: there is no
group-to-warehouse grant table — `group_service_role` grants *services*,
not warehouses, and `group_config.context` is a location preset, not an
authorization catalog — so a shared constant is still the right source, it
just needs a side-effect-free home. Phase 4 introduces a new
`core/uchoice_constants.py` with `VALID_WAREHOUSE_CODES = frozenset({"JFK",
"DE"})`, and **both** `jobs/uchoice_daily.py` (replacing its local
`WAREHOUSES = ["JFK", "DE"]`, `jobs/uchoice_daily.py:28`) and this phase's
role-change validators/handler import from that shared module — the
dependency now points from the job into the domain constant, not the
reverse.

`_last_admin_protection` is unaffected by any of this and stays in force
for every real demotion, including a hypothetical admin → non-operational
transition.

## 5. Assignable-role question — resolved: exclude `pending`

Round 35 raised this without resolving it; **Codex's round-37 review
resolves it explicitly (§1): `pending` is excluded from `role_change`'s
assignable-role set.** Rationale, adopted as-is: the admin API already
supports real member suspension via `group_member.is_active = False`;
allowing `role_change` to also assign `pending` would create a second,
ambiguous suspension mechanism doing the same job through a different
state. `pending` stays system-assigned-only, reachable exclusively through
self-registration (§3) — never through `role_change`, in either direction.

This is now a hard invariant, not a prompt hint. **Defined precisely per
Codex round-41 finding 2**: rather than deriving the assignable set as
"every operational role except `pending`" (which would silently expose any
future internal/system role the moment it's added, since it wasn't
`pending`), `core/uchoice_constants.py` (the same new module from §4)
additionally defines an explicit positive allowlist,
`ASSIGNABLE_ROLE_NAMES = {"admin", "customer", "warehouseman",
"accountant"}` — the exact four operational roles that exist today. All
three of §4's boundaries (before-persistence, pre-confirm, execution) must
check `new_role in ASSIGNABLE_ROLE_NAMES`, not an exclusion rule. The
migration/AI prompt may still describe these four choices to the user in
natural language, but every validation layer consumes the server-side
allowlist, not a "not pending" inference. The existing `is_active`-based
admin suspension flow is unchanged by this phase.

## 6. Scope boundary

- Does not touch `access_control.check_access`'s core semantics for
  already-registered members — the new pre-access command is a new branch
  entered *before* that function is reached for the exact-command case, and
  the pending-short-circuit (§3.7) is a new branch entered *after*
  `check_access` succeeds but *before* `ai_chain.process`. Neither replaces
  or modifies existing access-control logic for non-pending roles.
  `check_access` itself is untouched.
- Does not add a de-registration/removal-request self-service path — an
  admin still removes members exclusively via the existing admin API.
  Not assumed needed; out of scope unless raised separately.
- Does not change how `member_candidates` builds its list
  (`core/uchoice_context.py`) — round 35 confirmed pending members already
  appear there unmodified, since it queries every `group_member` regardless
  of role.

## 7. Work division

Single-writer, same reasoning as Phase 2/3: this phase edits
`api/webhook.py`'s pre-access dispatch (new code, adjacent to but not
overlapping Phase 2/3's `workflow_engine.py`/`pdf_stub.py` surface),
`core/pre_confirm_validators.py` (already Claude Code's file from Phase 1/2),
and a new migration for the `pending` role seed row. **Claude Code** takes
implementation; Codex's role is design/test review, as in Phase 2/3.

**Full production-file surface, updated per Codex round-41 finding 1 to add
the new shared constants module and the job's switch to it**:

- New forward migration: seed the `pending` role row. No column/table
  changes needed — `role`, `group_member`, `group_service_role` all support
  this without alteration.
- New `core/uchoice_constants.py` — `VALID_WAREHOUSE_CODES` and
  `ASSIGNABLE_ROLE_NAMES`, the two side-effect-free authorization constants
  from §4/§5.
- `jobs/uchoice_daily.py` — replace its local `WAREHOUSES = ["JFK", "DE"]`
  (`jobs/uchoice_daily.py:28`) with an import of
  `core.uchoice_constants.VALID_WAREHOUSE_CODES`, so both consumers share
  one definition.
- `api/webhook.py` — new pre-access exact-command branch (§3.1-3.5) and the
  post-access pending short-circuit (§3.7), both in `_process_message`.
- New `core/self_registration.py` (or similar) — the deterministic
  command-recognition + insert-with-race-handling logic, kept out of
  `api/webhook.py` itself so it's independently unit-testable without a
  live webhook request.
- `core/workflow_engine.py` — extend
  `_sanitize_extracted_fields_before_persistence` for `role_change`'s
  `target_openid`/`new_role` fields, per §4 boundary 1.
- `core/pre_confirm_validators.py` — extend `role_change`'s composed
  validator per §4 boundary 2 (warehouse_code-required-for-warehouseman,
  unknown/non-assignable-role rejection, `pending`-exclusion per §5), both
  checks consuming `core.uchoice_constants`.
- `handlers/uchoice/role_change.py` — add the execution-time backstop per
  §4 boundary 3 (re-validated membership/role/warehouse immediately before
  the mutation), also consuming `core.uchoice_constants`.
- Dedicated Phase 4 tests per §8.

## 8. Required tests (per Codex round 35 and round 37, adopted in full)

Fixture-first, same discipline as Phase 1/2/3 — written against current
(missing) behavior first, confirmed to fail for the right reason, then
implementation:

- the exact self-registration command (`注册成员`, after NFKC + whitespace
  normalization) registers only the sender into `pending` for that specific
  active group; whitespace padding and full/half-width variant input still
  match after normalization;
- a substring, or the command plus extra surrounding prose, does **not**
  match (fails closed);
- arbitrary text, single (non-group) chats, unknown/inactive groups, and an
  empty sender OpenID create nothing;
- **corrected per Codex round-39 minor wording note**: the bot receives the
  same sender's exact command from two different active WeCom group chats
  (the sender is *absent* from `group_member` in both — that absence is the
  precondition being tested, not prior membership) — each produces its own
  `pending` row, correctly scoped per group — but nothing in the message
  body can register a *different* OpenID than the sender's
  own;
- a retry of the exact command by an already-`pending` sender, and a
  simulated concurrent composite-PK duplicate insert, both leave exactly
  one `group_member` row and return the "already registered; awaiting
  administrator assignment" response;
- a retry by a sender who is already an *operational* (non-`pending`)
  member gets the distinct "already registered" response (no "awaiting
  assignment" wording), and their existing role is unchanged;
- a database failure that is **not** the composite-PK integrity violation
  (e.g. the `pending` role row missing, or a simulated FK failure) rolls
  back and returns the generic failure response — not mislabeled as a
  duplicate;
- a `pending` member's non-command messages never reach `resolve_session`,
  `build_context`, or `ai_chain.process` (mock/spy assertion on all three,
  not just "no crash") and get the fixed awaiting-administrator reply;
- `pending` has zero `group_service_role` grants, confirmed via
  `access_control.check_access` returning an empty `allowed_services`
  list for it;
- successful registration creates no `conversation_session` and no
  `request_log` row;
- an admin sees the pending candidate via `member_candidates` and can
  promote it via `role_change`; a non-admin cannot trigger `role_change` at
  all (pre-existing access-control behavior, asserted here for regression
  safety since this phase touches the same promotion path);
- `role_change` rejects `pending` as a `new_role` target at all three
  boundaries (before-persistence, pre-confirm, execution) — new tests for
  the §5 resolution;
- a fabricated `target_openid` (not a real group member) and a fabricated
  `new_role` (not a real/assignable role) are each rejected — asserted
  separately at before-persistence, pre-confirm, and execution, confirming
  none of the three boundaries alone is load-bearing;
- promoting to `warehouseman` without a `warehouse_code`, and with a
  `warehouse_code` naming a code outside the shared valid U-Choice
  warehouse set (`core.uchoice_constants.VALID_WAREHOUSE_CODES`), are both
  rejected at the execution backstop even when constructed to bypass
  pre-confirm (new tests for the §4 fix — corrected wording per Codex
  round-41 finding 1);
- `jobs/uchoice_daily.py` still functions correctly after switching to
  `core.uchoice_constants.VALID_WAREHOUSE_CODES` (regression check for the
  constant extraction);
- the existing `is_active`-based admin suspension flow is unchanged by this
  phase (regression check, since §5 explicitly keeps it as the sole
  suspension mechanism);
- `_last_admin_protection` still holds for a demotion attempt on the
  group's sole admin — regression check, this phase must not weaken it;
- the existing layered operational-client/transport blocks
  (`tests/conftest.py`) remain in force for every new test — no real
  WeChat/GPT call.

## 9. Remaining open items before sign-off

1. Final user approval — no implementation
   begins before that, same two-stage rule as Phase 1/2/3.

## Sign-off

- Claude Code: round-36 original draft incorporated Codex's round-35 audit
  in full. Round-38 incorporated Codex's round-37 review (three-boundary
  role_change hardening, pending-exclusion, duplicate/error semantics,
  normalization). Round-40 incorporated Codex's round-39 review (fixed the
  §3.1/§3.5 registration-placement contradiction; named the
  `group_member_pkey` duplicate constraint; completed §7's file list). This
  round-42 revision incorporates Codex's round-41 review in full: extracted
  the warehouse constant out of `jobs/uchoice_daily.py` into a new
  side-effect-free `core/uchoice_constants.py` (`VALID_WAREHOUSE_CODES`),
  fixing the bad dependency direction Codex flagged (core validation must
  not import a scheduled-job module with a bound WeChat client); added an
  explicit `ASSIGNABLE_ROLE_NAMES` positive allowlist to the same module,
  replacing the "every role except pending" exclusion rule that would have
  silently exposed any future internal/system role; updated §7's file
  surface and §8's stale wording accordingly. Every technical claim was
  independently verified against the current repository before inclusion
  (`api/webhook.py`, `models/group.py`, `handlers/uchoice/role_change.py`,
  `core/pre_confirm_validators.py`, `core/workflow_engine.py`,
  `jobs/uchoice_daily.py`) — not taken on Codex's word alone.
- Codex: **signed, round 43** — independently re-read the round-42 document
  and confirmed the routing order, exact command, constrained duplicate
  handling, three-boundary role-change validation, shared allowlists, complete
  production-file surface, and security tests match the prior review rounds.

**NOT YET APPROVED by the user.** No implementation may begin.
