# Plan: Smart Robot → WeChat Kefu migration (staff-facing)

Status: **SIGNED PLAN v7 (Claude Code round 77; Codex sign-off round 78).
Not approved by the user. No production, migration, or test file has been
touched for this work.** Both agents agree on the plan. The user's separate
implementation approval is required before any production work.

This document assumes the reader has `kefu-migration-context.md` and
`discussion.md` rounds 54-76 already loaded. It does not re-derive
background already settled there.

**Changes from v6, per Codex's round-76 review**: v6 incorrectly removed
the `NOT EXISTS` unexpired-claim guard when adding the per-identity
advisory lock, reasoning the lock alone was sufficient. It isn't — the
lock only serializes the short claim *transactions*; once released at
commit, a second worker could immediately claim the *next* message while
the first was still being processed, letting two messages from the same
identity process concurrently, which is exactly what per-identity
serialization exists to prevent. **v7 restores the guard alongside the
lock** — the lock makes the guard's snapshot reliable, the guard is what
actually blocks a second claim while the first is outstanding; neither
alone is sufficient (§2.6). Corrected the two-connection test to assert
the second connection gets no row at all while the first's claim is
outstanding, not merely that some claim succeeds (§10).

**Changes from v5, per Codex's round-74 review**: replaced the v5 claim
query's original `NOT EXISTS`-only design — which does not serialize
concurrent snapshots by itself — with the addition of a per-identity
PostgreSQL transaction-level advisory lock (`pg_advisory_xact_lock`)
acquired *before* the claim selection runs (§2.6); added a deterministic
`msgid` tiebreak to the ordering; added a real two-connection database
concurrency test to §10, replacing what would otherwise be an insufficient
sequential mock.

**Earlier, from v4 per Codex's round-72 review**: added the durable
staff→current-case binding (`kefu_staff_case_context`) so a staff member's
unqualified follow-up message continues whatever case they're currently in
without repeating the case number every turn, with explicit switch/clear
transitions (§2.5); tightened the file-payload CHECK to require
`artifact_key` explicitly, and added regenerated-artifact hash
verification against the stored `payload_hash` before send (§2.6); updated
the worker→case-turn-service interface to pass an optional case-number
*hint* rather than an unconditional case identifier (§11.1).

## 1. What is actually changing

The entire staff-facing side of the bot moves from Smart Robot (WeCom-only)
to WeChat Kefu (ordinary consumer WeChat) — **round 61 item 1: everything,
not just customer intake.** Customers never interact with the bot at all,
under any channel. Staff:

1. Reaches the bot 1:1 via a single shared Kefu account, using their own
   personal WeChat.
2. States which customer a request/case is for (customer selection is
   explicit — round 58/61).
3. Works the case through the same `session_manager`/`workflow_engine`
   pipeline as today, gets a proper response.
4. Manually relays that response to the actual customer through whatever
   channel they already use. The bot never talks to a customer directly.

The Smart Robot code path is **not deleted** in this phase. Kefu is
additive; Smart Robot's FedEx/UPS flows (separate, currently inactive) are
untouched.

## 2. Data model changes

### 2.1 `uchoice_customer` (new table)

```
uchoice_customer
  customer_id      uuid PK, gen_random_uuid()
  customer_code    text, UNIQUE, NOT NULL
  canonical_name    text, not null — display/search data only, never an
                    identity key
  aliases           text[], for fuzzy matching (nullable, empty by default)
  is_active         boolean, default true
  created_at / updated_at
```

No `default_warehouse_code` column. `JFK Warehouse`, `DE Warehouse`, the
self-pickup placeholders, and `散客` are real rows in this table — not a
special case (round 63, final).

### 2.2 `uchoice_address.customer_id`

`ALTER TABLE uchoice_address ADD COLUMN customer_id uuid REFERENCES
uchoice_customer(customer_id)`. Backfilled from the 33 existing rows
grouped by `company_name`. The 5 rows with `company_name IS NULL` are
excluded from automatic backfill and block the final `NOT NULL` migration
until manually classified — no permanent "unassigned" production state.

`core/uchoice_context.address_candidates(db, customer_id)` — filtered, no
`is_global` flag anywhere. `upsert_address` takes an authoritative
`customer_id`, never creates/reassigns identity from a model-generated
string.

### 2.3 Staff/channel identity — unambiguous scope, unambiguous role-change dispatch

**Codex round-68 finding 4, addressed: v2's uniqueness key permitted the
same `(open_kfid, external_userid)` to exist in more than one group, while
access resolution only ever looked up by that pair — genuinely ambiguous.**

Resolved by picking one explicit answer rather than leaving it open: **for
this migration, one `open_kfid` maps to exactly one `group_id` (the single
U-Choice tenant), fixed at deployment configuration time, never chosen by
staff or inferred from a message.**

```
kefu_staff
  staff_id          uuid PK
  open_kfid          text, not null
  external_userid    text, not null
  group_id           uuid FK -> group_config, not null — populated from
                     the fixed open_kfid -> group_id mapping above, not
                     looked up ambiguously
  role_id            uuid FK -> role, not null (defaults to `pending`)
  warehouse_code      text, nullable — same invariant as
                     `GroupMember.warehouse_code`: required for
                     role=`warehouseman`, cleared otherwise
  display_name       text, nullable
  is_active          boolean, default true
  created_at / updated_at
  UNIQUE (open_kfid, external_userid)  -- group_id deliberately excluded
                     from the uniqueness key: this identity is globally
                     unique per Kefu account, matching the fixed
                     one-account-one-tenant invariant above. A genuine
                     multi-tenant future (one open_kfid serving several
                     groups) is explicitly out of scope and would need
                     its own separately-designed change, not silently
                     supported by this schema.
```

Access resolution: `(open_kfid, external_userid)` → `kefu_staff` row →
`kefu_staff.group_id` + `role_id` → existing `group_service_role` grants.

**Role-change dispatch — tagged identity, not table-probing (Codex
round-68 finding 4: "identifiers can collide across `GroupMember` and
`kefu_staff`").** The candidate contract for `role_change`'s target is an
explicit tagged identifier, never a raw string resolved by checking which
table happens to contain a match:

```
target_identity = {kind: "smart_robot", key: <wechat_openid>}
                 | {kind: "kefu",        key: <staff_id (uuid)>}
```

`member_candidates` returns both `GroupMember` and `kefu_staff` rows, each
tagged with its `kind`. All three of Phase 4's hardened boundaries
(`_sanitize_role_change_fields_before_persistence`,
`_valid_role_change_target_and_role`, `RoleChangeHandler`) parse the
tagged identity and dispatch to the matching table's own
membership/assignable-role/warehouse checks — never inferred from which
table an untyped ID happens to match. If this dispatch proves too invasive
at implementation time, the fallback is the existing admin HTTP API acting
on `kefu_staff` directly, still tagged, still unambiguous.

**`customer` stays in `ASSIGNABLE_ROLE_NAMES` for this phase.** Smart
Robot keeps running throughout this migration; legacy `customer`-role
`GroupMember` rows/grants remain valid. New intake grants move to
`warehouseman` (round 64); retiring `customer` itself is a separate, later,
explicitly-approved decision.

### 2.4 Case/request identity — actor/requester split, and the `get_original_fields()` fix

```
ALTER TABLE request_log
  ALTER COLUMN wechat_openid DROP NOT NULL,
  ADD COLUMN customer_id           uuid REFERENCES uchoice_customer(customer_id),
  ADD COLUMN submitted_by_staff_id uuid REFERENCES kefu_staff(staff_id),
  ADD COLUMN source_channel        text NOT NULL DEFAULT 'smart_robot'
             CHECK (source_channel IN ('smart_robot', 'kefu')),
  ADD COLUMN origin_session_id     uuid REFERENCES conversation_session(session_id);

ALTER TABLE interaction_log
  ALTER COLUMN wechat_openid DROP NOT NULL,
  ADD COLUMN customer_id           uuid REFERENCES uchoice_customer(customer_id),
  ADD COLUMN submitted_by_staff_id uuid REFERENCES kefu_staff(staff_id),
  ADD COLUMN source_channel        text NOT NULL DEFAULT 'smart_robot'
             CHECK (source_channel IN ('smart_robot', 'kefu'));
```

**Both `ALTER`s spelled out explicitly (Codex round-68 finding 1: v2
"only sketches the `request_log` ALTER" and never showed
`interaction_log`'s).**

**Real bug fix**: `core/uchoice_context.get_original_fields()`
(`core/uchoice_context.py:200-210`) currently resolves a request's
original `collected_fields` by filtering `ConversationSession` on
`wechat_openid=target.wechat_openid`, to disambiguate the original
submission session from a later completion session sharing the same
`request_log_id`. For a Kefu-originated request, `wechat_openid` is
null/unused, so this silently returns nothing. Fix: `request_log
.origin_session_id` (above) is set once, at creation time, in
`_handle_new_request`; `get_original_fields` looks up by that FK directly
instead of matching on actor identity — channel-agnostic, and simpler for
Smart Robot rows too.

### 2.5 `ConversationSession` — channel identity, per-turn actor audit, and a durable execution ledger

**Codex round-68 finding 1, addressed: v2 never defined the Kefu session's
own channel/creator identity, and "records the actual acting staff_id" had
no durable schema — conversation history stores only role/content.**

```
ALTER TABLE conversation_session
  ALTER COLUMN wechat_openid DROP NOT NULL,  -- group_id stays NOT NULL;
                                              -- Kefu sessions populate it
                                              -- from kefu_staff.group_id
                                              -- at open time, same as
                                              -- Smart Robot sessions do
                                              -- today
  ADD COLUMN source_channel      text NOT NULL DEFAULT 'smart_robot'
             CHECK (source_channel IN ('smart_robot', 'kefu')),
  ADD COLUMN opened_by_staff_id  uuid REFERENCES kefu_staff(staff_id),
  ADD COLUMN case_revision       integer NOT NULL DEFAULT 0,
  ADD COLUMN customer_id         uuid REFERENCES uchoice_customer(customer_id),
  ADD COLUMN case_number         text UNIQUE;
```

`case_number` is generated at session creation using the same proven
`generate_serial_number()` pattern already used for `request_log
.serial_number`, with a distinct prefix — gives staff a stable, pasteable
identifier from the very first turn, before any `request_log` row exists.

**New `case_turn` table — the actual per-turn actor audit (Codex round-68
finding 1: "define a `case_turn`/actor-audit row ... make its insert
atomic with the revision-checked session update").** This is distinct from
`interaction_log`, which keeps its own existing purpose (funnel/efficiency
analysis, one row per classified intent) — `case_turn` is the durable
record of who said what, in what turn, for a given case:

```
case_turn
  turn_id                uuid PK
  session_id              uuid FK -> conversation_session, not null
  case_revision            integer, not null -- the revision this turn
                          produced
  acting_staff_id           uuid FK -> kefu_staff, nullable
  acting_wechat_openid      text, nullable -- for Smart Robot turns,
                          mirrors today's actor concept
  role                    text not null -- user | assistant
  CHECK (
    (role = 'user' AND num_nonnulls(acting_staff_id, acting_wechat_openid) = 1)
    OR
    (role = 'assistant' AND acting_staff_id IS NULL AND acting_wechat_openid IS NULL)
  ) -- Codex round-70 finding 4: v3's constraint required an actor even
    -- for the bot's own replies, which have none
  content                  text not null
  source_message_id        text, unique, nullable -- Kefu's msgid for the
                          inbound message that produced this turn; null
                          for Smart Robot turns and for the assistant-role
                          row (only the user-role row that consumed an
                          inbound Kefu message carries it
  reply_text                text, nullable -- stored so a duplicate
                          `source_message_id` can be answered from this
                          row without recomputation, see below
  customer_copy_text        text, nullable
  artifact_keys              text[], nullable -- §11.2's Artifact.artifact_key
                          values produced by this turn, if any
  created_at                timestamptz not null default now()
```

**`source_message_id` closes a real gap Codex found (round-70 finding 1):
a claim lease is concurrency control between two workers, not end-to-end
idempotency against one worker retrying after a crash.** If the case-turn
transaction commits successfully but the worker crashes before marking
`kefu_inbound_message.status='processed'`, the reclaimed message gets
handed to `process_case_turn` again. `source_message_id`'s `UNIQUE`
constraint is what makes this safe: **before running any extraction,
mutation, or execution, the case-turn service checks whether a `case_turn`
row already exists for this `msgid`.** If one does, it returns the
already-stored `reply_text`/`customer_copy_text`/`artifact_keys` directly
— no re-extraction, no second CAS attempt, no duplicate delivery enqueued.
This is why `reply_text` etc. are persisted on the row itself, not just
the fact that a turn happened: a duplicate call needs to be *answerable*
from stored data, not merely detectable.

**Concurrency mechanism — the CAS write and the turn-audit insert happen
in one transaction:**

1. Load the case by `case_number`, capture `case_revision` and `status`.
2. **Check `source_message_id` first (the idempotency check above) before
   doing anything else** — a hit short-circuits the rest of this sequence
   entirely.
3. Run extraction/AI/validation without holding a row lock.
4. In one database transaction: `UPDATE conversation_session SET
   conversation_history = :merged_history, collected_fields =
   :merged_fields, status = :new_status, case_revision = case_revision + 1
   WHERE session_id = :id AND case_revision = :expected AND status =
   :expected_status`, **and** `INSERT INTO case_turn (...)` including
   `source_message_id` and the computed `reply_text`/etc. — both succeed
   or both roll back.
5. Zero rows affected by the `UPDATE` → roll back the whole transaction,
   discard the stale turn, tell staff the case changed, show latest state.

This replaces today's separate-commit `add_message`/`update_collected_fields`
helpers for the Kefu path specifically; Smart Robot's existing calls are
untouched.

**Durable execution ledger, replacing v2's timeout-based reclaim (Codex
round-68 finding 2: "a worker can complete a DB mutation or external side
effect and crash before recording the terminal case state; a second
worker then re-runs it" — a real violation of the plan's own zero-
duplicate-execution rule):**

```
case_execution
  execution_id       uuid PK
  session_id           uuid FK -> conversation_session, not null
  execution_key         text, UNIQUE, not null -- one logical execution
                       per confirmation attempt, e.g. derived from
                       (session_id, case_revision at claim time)
  status                text not null default 'claimed'
                       -- claimed | db_committed | completed | failed
  claimed_by             text, not null
  claimed_at              timestamptz, not null, default now()
  lease_expires_at        timestamptz, not null
  heartbeat_at             timestamptz, nullable
  db_committed_at           timestamptz, nullable -- when the DB-phase
                       mutation (Phase 2's "DB phase") committed
  completed_at              timestamptz, nullable -- when side effects
                       (PDF delivery, reply) also finished
  last_error                text, nullable
```

Confirmation claims execution by inserting a `case_execution` row keyed on
`execution_key` (unique — a second claim attempt for the same logical
execution fails the insert, giving the same single-winner guarantee as
before) in the same transaction as the `pending_confirmation → processing`
CAS update.

**Atomicity invariant, stated explicitly (Codex round-70 finding 2:
"`db_committed_at IS NULL` proves nothing unless the business mutation and
the `case_execution -> db_committed` transition commit in the same DB
transaction"):** the workflow's DB-phase mutation (storage write, request
status change — Phase 2's existing "DB phase") and the `case_execution
.status = 'db_committed', db_committed_at = now()` update **are the same
database transaction, always.** There is no intermediate state where the
business mutation is committed but `db_committed_at` is still null, or
vice versa — the two either both land or both roll back together. This is
what makes `db_committed_at IS NOT NULL` a valid proof, not an assumption.

**Recovery distinguishes what's actually provable, reusing Phase 2's
already-signed DB-phase/side-effect-phase split rather than inventing new
semantics:**

- Lease expired, `status='claimed'`, `db_committed_at` still null → the
  DB-phase mutation never happened (guaranteed by the atomicity invariant
  above). Mark `case_execution.status='failed'`. **In the same
  transaction, also transition the session (`conversation_session
  .status`) from `processing` back to `pending_confirmation`, via a
  revision-checked update** — this is the specific gap Codex named
  ("session is still processing; a fresh key cannot be claimed without a
  specified CAS/status recovery path"). Once both are marked, a fresh
  claim attempt (new `execution_key`) is possible because the session is
  back in a claimable state and nothing was ever persisted to duplicate.
- Lease expired, `status='db_committed'` but not `completed` → the DB
  mutation **already succeeded and must never be re-run.** Recovery
  resumes only the side-effect phase (retry PDF generation/Kefu delivery)
  against the already-committed result. The session stays `processing`
  throughout — it only ever reaches its terminal success state once
  `case_execution.completed_at` is set.
- `status='failed'` is a terminal state surfaced to whoever next
  references the case, not silently retried forever.

**Remote-send delivery is at-least-once, not exactly-once — stated
honestly rather than implied otherwise (Codex round-70 finding 2):** a
local `idempotency_key` prevents *this system* from enqueueing a duplicate
send, but it cannot guarantee Kefu itself never receives a message twice —
if the process loses the response after Kefu already accepted a send
(before `provider_message_id` is recorded), a retry will send again, and
nothing in this design (absent a verified provider-side reconciliation
API, which has not been confirmed to exist) can prevent that. **The
adopted policy: business/DB steps remain exactly-once, guaranteed by the
`case_execution` ledger and the single-transaction invariant above. The
remote send itself is at-least-once, with a narrow, documented duplicate-
message risk window** (specifically: response lost after Kefu accepted the
send, before `provider_message_id` was durably recorded) **rather than
at-most-once with a silent-loss risk.** This is a deliberate choice, not
an oversight — losing a staff-facing message silently is worse than an
occasional duplicate.

**Staff→current-case binding — new, closes a real product gap (Codex
round-72 finding: `opened_by_staff_id` says who created a case, not which
shared case a *different* staff member is currently continuing).** Round
61 item 5 settled that any authorized staff member can reference a case
and continue it — but that was never wired to what happens on their *next*
message once they've done so. Without a durable binding, staff would have
to repeat the case number on every single turn, which nobody actually
proposed or wanted.

```
kefu_staff_case_context
  staff_id            uuid PK REFERENCES kefu_staff(staff_id)
  active_session_id   uuid REFERENCES conversation_session(session_id), nullable
  updated_at          timestamptz not null default now()
```

**Transitions:**

- A staff member opens a brand-new case → their `kefu_staff_case_context`
  row is upserted with `active_session_id` set to the new session.
- A staff member references an authorized `case_number` (their own or
  another staff member's) → this **atomically switches** their binding to
  that session, via an `UPSERT ... SET active_session_id = :session_id`.
  This is the only place `kefu_staff_case_context` is written from a
  case-reference turn; it never happens implicitly.
- A case reaches a terminal or cancelled state → every
  `kefu_staff_case_context` row currently bound to that `session_id` is
  cleared (`active_session_id = NULL`) in the same transaction that
  finalizes the case. More than one staff member can be bound to the same
  case at once (round 61 item 5's shared-access model), so this is a
  `WHERE active_session_id = :session_id` update, not a single-row clear.
- An **unqualified turn** (the staff member's message contains no explicit
  case-number reference) uses their currently-bound
  `kefu_staff_case_context.active_session_id`, if any and if that case
  isn't already terminal, as the target case. If there's no binding (or
  it's stale/terminal), the turn is treated as opening a new case.
- `customer_id` stays locked on the case itself
  (`conversation_session.customer_id`, §2.4-2.5) — **never copied into
  `kefu_staff_case_context`**, so there's no second place for it to drift
  out of sync.
- Per-turn reauthorization (service/scope/role/warehouse, re-checked every
  turn regardless of who opened the case) is unchanged by this — the
  binding only resolves *which* case a message targets, it never
  substitutes for the authorization check that already happens on every
  turn.

### 2.6 Kefu transport/sync state — lease-based claim recovery, precise transaction contract, real XOR

**Codex round-68 finding 3, addressed: v2's inbound claims had no crash
recovery (`claimed` rows with no lease could be stuck forever), and the
cursor/insert transaction relationship was asserted, not specified.**

```
kefu_sync_cursor
  open_kfid     text PK
  cursor        text, not null
  updated_at

kefu_inbound_message
  msgid            text PK
  open_kfid        text, not null
  external_userid  text, not null
  payload          jsonb, not null -- normalized content, enough to
                    replay processing from this row alone
  received_at      timestamptz, not null, default now()
  processed_at     timestamptz, nullable
  status           text not null default 'pending'
                    -- pending | claimed | processed | failed
  claimed_by       text, nullable
  claimed_at       timestamptz, nullable
  lease_expires_at timestamptz, nullable
  attempt_count    integer, not null, default 0
  last_error       text, nullable
```

**Cursor/insert transaction contract, made precise (Codex's explicit
ask):** a single `sync_msg` page's messages are inserted into
`kefu_inbound_message` **and the cursor advanced in one database
transaction.** If that transaction fails partway, nothing commits and
`kefu_sync_cursor` is unchanged, so the next poll re-fetches the exact same
page from Kefu. Re-inserting an already-processed page is safe because
`msgid` is the primary key — the insert uses `ON CONFLICT (msgid) DO
NOTHING`, so a retried fetch after a partial failure never creates
duplicate rows or re-triggers processing of an already-`processed` message.

**Claim serialization, corrected three times now — per-identity (round
72), race-free under concurrent snapshots (round 74), and now: the lock
and the unexpired-claim guard are both required together, not either
alone (Codex round-76 finding).** v6 removed the `NOT EXISTS` guard when
adding the advisory lock, reasoning that the lock alone was sufficient.
It isn't: the lock only serializes the short claim *transactions* — once
worker A's transaction commits (claim done) and releases the lock, worker
B immediately acquires it and, with `NOT EXISTS` gone, claims the *next*
pending message right away, **while A's message is still being
processed.** Two messages from the same identity then process
concurrently — exactly the outcome per-identity serialization exists to
prevent. The lock fixes snapshot staleness; only the guard actually blocks
a second claim while the first is still outstanding. Both are required,
each doing a different job:

```sql
-- held only for the duration of this transaction, released automatically
-- on commit/rollback — not held across the actual message processing
SELECT pg_advisory_xact_lock(hashtextextended(:open_kfid || ':' || :external_userid, 0));

UPDATE kefu_inbound_message
SET status = 'claimed', claimed_by = :worker, claimed_at = now(),
    lease_expires_at = now() + interval '<lease>'
WHERE msgid = (
  SELECT msgid FROM kefu_inbound_message
  WHERE open_kfid = :open_kfid AND external_userid = :external_userid
    AND (status = 'pending' OR (status = 'claimed' AND lease_expires_at < now()))
    AND NOT EXISTS (
      SELECT 1 FROM kefu_inbound_message m2
      WHERE m2.open_kfid = :open_kfid AND m2.external_userid = :external_userid
        AND m2.status = 'claimed' AND m2.lease_expires_at >= now()
        AND m2.msgid <> kefu_inbound_message.msgid
    )
  ORDER BY received_at ASC, msgid ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

**Why both are necessary, precisely:** the advisory lock is what makes the
`NOT EXISTS` subquery's snapshot *reliable* — because worker B cannot even
begin evaluating it until worker A's transaction (holding the same lock)
has fully committed or rolled back, B always sees A's claim if it's still
outstanding. Without the lock (v5), two workers could evaluate `NOT
EXISTS` against snapshots taken before either committed, each seeing "no
claim," and both proceed. Without the guard (v6), the lock only prevents
that specific snapshot race but does nothing to stop a *second, later*
claim once the first transaction is done — it would happily let B claim
the next message immediately, with no relationship to whether A finished
processing. Together: B blocks on the lock while A claims message 1; once
A commits, B proceeds, but `NOT EXISTS` now correctly finds A's still-
`claimed`-and-unexpired row and returns nothing for B to claim, **until A
marks its message `processed`/`failed` or A's lease expires** — which is
the actual per-identity serialization guarantee this section exists to
provide.

The lock only needs to cover selection-through-claim-commit; it is not
held across the actual AI/workflow processing that follows, and the
durable claimed row plus its lease (below) is what governs later claims
during that processing window.

A worker crash after claiming leaves the row reclaimable once its lease
expires — no message is ever stuck permanently. A long-running worker must
renew (`heartbeat`/extend `lease_expires_at`) before its lease would
otherwise expire, or accept that an expired-but-still-in-progress claim
becomes eligible for reclaim — the same overlap the `case_execution`
ledger (above) and `case_turn.source_message_id` idempotency check (§2.5)
already resolve safely regardless of which worker "wins." Processing is
serialized per `(open_kfid, external_userid)` so two callback batches for the same
staff identity cannot race one session.

**`kefu_outbound_delivery` — true XOR, not "at least one" (Codex round-68
finding 3):**

```
kefu_outbound_delivery
  delivery_id        uuid PK
  session_id         uuid REFERENCES conversation_session(session_id), nullable
  request_log_id     uuid REFERENCES request_log(log_id), nullable
  CHECK (num_nonnulls(session_id, request_log_id) = 1)  -- exactly one,
                       not "at least one"
  recipient_staff_id  uuid REFERENCES kefu_staff(staff_id), not null --
                       Codex round-70 finding 3: this must be the specific
                       staff member this delivery is *for*, immutable at
                       creation time — never derived from
                       `session.opened_by_staff_id` at send time, since any
                       authorized staff member may be the one who actually
                       triggered the response on a shared case. Resolved
                       to a Kefu send address via
                       `kefu_staff.open_kfid`/`external_userid` at send
                       time (both are immutable identity fields on
                       `kefu_staff`, so this join is always current).
  idempotency_key     text, unique, not null
  payload_type        text, not null -- text | file
  text_content         text, nullable -- the actual deferred text, for
                       payload_type='text'; durable, not reconstructed
  artifact_request_log_id uuid REFERENCES request_log(log_id), nullable
  artifact_doc_type    text, nullable
  artifact_key          text, nullable -- together, these three columns
                       are a stable reconstruction reference for
                       payload_type='file': Phase 3's PDF generation is
                       already logically idempotent, so the file is
                       regenerated on demand from
                       (request_log_id, doc_type) rather than storing raw
                       bytes in this table. Codex round-70 finding 3: "in-
                       memory artifact bytes are insufficient for
                       window-closed and retry flows" -- this is the fix.
  CHECK (
    (payload_type = 'text' AND text_content IS NOT NULL
       AND artifact_request_log_id IS NULL)
    OR
    (payload_type = 'file' AND text_content IS NULL
       AND artifact_request_log_id IS NOT NULL AND artifact_doc_type IS NOT NULL
       AND artifact_key IS NOT NULL)  -- Codex round-72: the prose already
                                       -- called all three columns the
                                       -- stable reference; the CHECK now
                                       -- actually requires all three
  )
  payload_hash         text, not null -- computed over the payload at
                       enqueue time; for a file delivery, verified again
                       against the regenerated artifact's hash immediately
                       before send (Codex round-72) — a mismatch means
                       Phase 3's generation idempotency guarantee was
                       violated somehow, and the send is aborted as
                       `failed` rather than silently delivering different
                       content than was originally intended
  provider_message_id  text, nullable
  status               text not null default 'pending'
                       -- pending | sent | failed
  attempt_count        integer, not null, default 0
  next_retry_at        timestamptz, nullable
  sent_at              timestamptz, nullable
  last_error            text, nullable
  created_at / updated_at
```

## 3. Warehouse resolution

Two tiers only, for both services — explicit when stated, JFK otherwise
(round 64). No customer-default tier.

- Outbound: `_resolve_outbound_warehouse_default`
  (`core/workflow_engine.py:532-550`) reused unchanged.
- Inbound: new `_resolve_inbound_warehouse_default` (or the existing
  function generalized), same two-tier pattern.

## 4. PDF delivery — staff-only, as a channel-neutral artifact

- `handlers/uchoice/pdf_stub.py`'s document-content logic is unchanged
  (Phase 3's idempotency/stable-date rules govern *generation*, not
  *delivery*).
- The handler is refactored to return a plain artifact (bytes, filename,
  content-type) instead of immediately writing to `core/download_tokens.py`.
- Smart Robot's existing callers wrap the artifact in a token URL exactly
  as today.
- Kefu's path uploads the same artifact via Kefu's media-upload API, sends
  a `file` message, tracked through `kefu_outbound_delivery` via
  `request_log_id`.

## 5. Crypto/transport adapter

- Extract the existing AES/SHA1/PKCS7 primitives from
  `core/WXBizJsonMsgCrypt.py` into envelope-neutral functions.
- New `WXBizXmlMsgCrypt`, no format auto-detection in one shared entry
  point.
- Test fixtures: official GET verification, real encrypted POST, wrong
  signature, wrong CorpID, malformed XML, missing `Encrypt`, XXE-safety.

## 6. Kefu API integration surface, and customer-scope boundaries

### 6.1 Transport

- **Receive**: callback → `kefu_sync_cursor`-tracked `sync_msg` polling,
  durable insert + cursor advance in one transaction (§2.6).
- **Send**: `POST /cgi-bin/kf/send_msg`, `access_token` caching/refresh.
  Window/quota handling is specified in §11's transport contract, not
  handled ad hoc here.
- **Config**: new `WECHAT_KEFU_SECRET`, `WECHAT_KEFU_TOKEN`,
  `WECHAT_KEFU_ENCODING_AES_KEY`, `WECHAT_KEFU_OPEN_KFID`.
- **Callback route**: new, separate from `/webhook`, registered against
  `config.SERVER_BASE_URL` — still unconfirmed by the user (open since
  round 56).

### 6.2 Which services require a customer

**Customer-scoped (require and lock `customer_id`):**
`uchoice_outbound_request`, `uchoice_inbound_request`, customer address
maintenance (`upsert_address`).

**Staff/business-scope only (never force a customer selection):**
`adjust_storage`, `move_storage`, `recount_storage`,
`confirm_inbound_completion`, `confirm_outbound_completion`,
storage/invoice/digest queries, `role_change`.

### 6.3 Internal vs. customer-copy rendering

Two distinct render modes: an internal block (everything) and a
customer-copy block (only what's safe to paste to the actual customer).
The customer-copy block never contains internal-only fields by
construction, not by staff manually editing it down.

### 6.4 Feature flags — explicit booleans, not derived (Codex round-68 finding 5)

**Codex's correction: "derived from whether the full credential set is
present" makes a partially-configured channel silently disabled instead
of failing fast — self-contradictory.** Fixed design: `config.py` gains
explicit `SMART_ROBOT_ENABLED`/`KEFU_ENABLED` boolean env vars (not
inferred from anything). `SMART_ROBOT_ENABLED` defaults to `true` (matches
current live behavior — no regression for existing deployments that never
set this var). `KEFU_ENABLED` defaults to `false` until rollout begins.
When a flag is `true`, `config.py` requires that channel's complete
credential set via the existing `_require()` (fails fast on any missing
value). When `false`, that channel's credential vars are read without
`_require()`, and its modules (webhook route, client, scheduler
registrations) are not imported/wired into `main.py`/`api/webhook.py` at
all — an unconfigured disabled channel can never fail startup, and an
enabled-but-incomplete channel always does.

## 7. Notification strategy — pull, not push, with channel-aware coexistence

**Codex round-68 finding 5: v2's "disable the two scheduled jobs" directly
contradicted "Smart Robot stays fully operational" — disabling them
globally changes Smart Robot's own behavior before its services are
actually cut over.**

Fixed design: `jobs/uchoice_daily.py` and `jobs/uchoice_invoice.py` **stay
registered and running** as long as `SMART_ROBOT_ENABLED` is true — no
change to their scheduling. Their queries are filtered to
`source_channel = 'smart_robot'` only, so Kefu-originated `request_log`
rows never appear in a push meant for a WeCom group that Kefu never
touches. A given job is only fully retired once **all** U-Choice traffic
for that job's concern has cut over to Kefu (i.e. no `source_channel =
'smart_robot'` rows remain relevant) — an explicit, later, separately-
confirmed step per service, matching §9's no-big-bang-cutover principle.
The new Kefu digest is pull-only and entirely separate, unaffected by any
of this.

**Daily digest as a real service**: new `service_type` + handler,
following `handlers/uchoice/queries.py`'s existing pattern, with its own
grant.

**Pending-completion-notice audience**: shown to whichever staff member's
current message next touches that case's business/warehouse scope, not
only the original submitter. Each case tracks whether its notice has
already been shown (`completion_notice_shown_at`) so it isn't repeated
indefinitely.

## 8. MVP scope boundaries

- **Text-only.** Non-text input gets a clear, controlled rejection.
- **U-Choice only.** No FedEx/UPS service moves to Kefu in this phase.
- **No admin case dashboard.** Case lookup happens entirely through Kefu
  conversation (`case_number` reference).

## 9. Rollout sequencing

1. Build shared transport/identity plumbing behind disabled configuration
   (§6.4) — nothing user-facing changes yet.
2. Validate against recorded callback/`sync_msg`/`send_msg` fixtures (§5)
   before any live callback is registered.
3. Pilot: small number of real staff identities, one warehouse scope,
   `uchoice_outbound_request` end-to-end including PDF delivery, before
   `uchoice_inbound_request`, before the rest of the staff service surface.
4. Smart Robot stays fully operational throughout (§7) — each service
   flips over individually, not a big-bang cutover.

## 10. Test strategy — full acceptance matrix

Fixture-first, same discipline as Phase 1-4.

- Clean and dirty address backfill, including the 5 null-`company_name`
  rows and the block on final `NOT NULL` migration.
- Customer isolation: a request for customer A never offers/accepts
  customer B's address.
- Pending-registration → role/warehouse grant flow for `kefu_staff`,
  including the tagged-identity `role_change` dispatch (§2.3).
- Role-change identity-collision test: a `GroupMember` and a `kefu_staff`
  row with colliding raw identifiers must never be confused — the tagged
  contract is what prevents it, tested explicitly (§2.3).
- Early `case_number` lookup, before any `request_log` row exists.
- Stale-turn CAS: a turn built against an outdated `case_revision` is
  discarded, `case_turn` insert rolls back with it.
- Simultaneous confirmation: two staff confirming the same case
  concurrently — exactly one `case_execution` row is created (unique
  `execution_key`), the other gets a clean rejection.
- **Execution crash recovery at each boundary**: crash before
  `db_committed_at` (safe fresh claim after the session is explicitly
  reverted to `pending_confirmation`, nothing to duplicate); crash after
  `db_committed_at` but before `completed_at` (resumes only the
  side-effect phase, never re-runs the DB mutation).
- **Commit-then-worker-crash duplicate `msgid`**: the case-turn
  transaction commits successfully, then the worker crashes before marking
  `kefu_inbound_message` processed; the reclaimed worker calls
  `process_case_turn` again for the same `msgid` — asserts the stored
  `case_turn.reply_text`/etc. is returned unchanged, with no second CAS
  attempt, no duplicate mutation, no duplicate delivery enqueued.
- **Inbound claim lease expiry and reclaim** — a crash after claiming a
  message leaves it reclaimable, not stuck.
- Cursor/insert transaction: a simulated partial failure mid-transaction
  leaves the cursor unchanged and re-fetching the same page is a safe
  no-op (`ON CONFLICT DO NOTHING`).
- Outbound delivery XOR constraint rejects a row with both or neither FK
  set.
- **Deferred payload survives a process restart**: a `window_closed`
  delivery's `text_content`/artifact reference is readable and correctly
  resendable after simulating a full process restart between the deferral
  and the staff member's next message.
- **Recipient correctness for a non-opening staff actor**: staff A opens a
  case, staff B later sends the message that produces a deferred delivery
  — asserts the delivery is addressed to staff B, never staff A.
- **`case_turn` assistant-row constraint**: an assistant-role row with a
  non-null actor is rejected; a user-role row with zero or two actors set
  is rejected.
- **Cross-staff case continuation without a cached revision**: a second
  staff member references an existing `case_number` they've never
  interacted with before, supplying no revision — asserts the service's
  own freshly-loaded revision is what's used, not a caller-supplied value.
- **Ambiguous remote-send outcome under the adopted policy**: a simulated
  "Kefu accepted the send, response lost before `provider_message_id` was
  recorded" scenario is handled per the documented at-least-once policy
  (a retry is permitted, not silently dropped, and the narrow duplicate-
  risk window is the asserted, understood behavior — not a bug).
- 48h-window/quota API errors, including the deferred-delivery-on-reopen
  flow (§11.3).
- Kefu-only and Smart-Robot-only startup, each failing fast on its own
  incomplete credentials when enabled, neither blocking the other.
- Scheduler coexistence: Smart Robot jobs keep running and only include
  `source_channel='smart_robot'` rows once Kefu traffic exists.
- XML adapter: signature/CorpID validation, malformed XML, missing
  `Encrypt`, XXE-safety.
- PDF media-upload/send failure after the underlying request already
  committed successfully — delivery failure, not operation failure.
- Pull-notice audience and no-repeat behavior.
- **Simultaneous same-staff messages, as a real two-connection database
  test, not a sequential mock (Codex round-74/76)**: two genuinely
  separate database connections, each holding two pending messages
  available for the same `(open_kfid, external_userid)`. Connection A
  claims message 1 and **commits**. Connection B then attempts a claim and
  **must return no row** — asserting the lock-plus-guard combination
  actually blocks B, not merely that B ends up claiming a different
  message (Codex round-76's explicit correction to the round-74/75 version
  of this test, which only checked that *a* claim succeeded, not that B
  was correctly blocked while A's claim was still outstanding). Only after
  A's message is marked `processed` (or its lease is made to expire) does
  a repeated attempt from B succeed in claiming message 2.
- **New-case-plus-follow-up ordering**: a staff member's case-opening
  message and their immediate follow-up are always processed in arrival
  order, never concurrently, never out of order.
- **Cross-staff case binding and explicit switch**: staff B references
  staff A's case, `kefu_staff_case_context` for B switches to it; B's next
  unqualified message continues that case without repeating the number.
- **Terminal clear**: a case reaching a terminal state clears the
  `active_session_id` binding for every staff member who had it bound, not
  just the original opener.
- **Customer-lock preservation across context switches**: switching a
  staff member's active-case binding never changes or copies the case's
  own locked `customer_id` — it stays exactly where §2.4-2.5 already put
  it.
- File-payload CHECK rejects a row missing `artifact_key` even when
  `artifact_request_log_id`/`artifact_doc_type` are present.
- Regenerated-artifact hash mismatch against stored `payload_hash` aborts
  the send as `failed` rather than delivering unverified content.
- **Full existing Smart Robot regression** — all 123 currently-passing
  tests still pass unchanged.

Acceptance requires: zero duplicate workflow execution under concurrency
or crash recovery, zero cross-customer address exposure, every
unprocessed inbound message remains replayable after a crash, in-order
processing per staff identity, and the full existing suite stays green.

## 11. Cross-agent interface contracts (new — Codex round-68 finding 6: "described, not fixed")

These are the exact call contracts at each of the three boundaries named
in §12's work division — agreed here, before either agent writes
implementation code, so both sides can build against a fixed interface
independently.

### 11.1 Codex's worker → Claude's case-turn service

```
process_case_turn(
  identity: {open_kfid: str, external_userid: str},
  message_content: str,
  message_meta: {msgid: str, received_at: datetime},
  case_number_hint: str | None,  # only set if the staff member's message
                                  # itself explicitly named a case number;
                                  # None otherwise. Never the caller's
                                  # notion of "current case" — that
                                  # resolution belongs entirely to the
                                  # service, see below.
) -> CaseTurnResult

CaseTurnResult =
    Success(reply_text: str, customer_copy_text: str | None,
            case_number: str, new_revision: int, artifacts: list[Artifact])
  | Stale(current_revision: int, current_state_summary: str)
  | Denied(reason: str)   # authorization failure — role/scope/warehouse,
                           # or an explicit case_number_hint that doesn't
                           # resolve to a real, authorized case
  | Error(message: str)   # unexpected failure, safe generic message
```

**`case_number_hint` renamed from `case_number`, and its resolution order
made explicit (Codex round-72's case-context finding, §2.5):** the service
— never the caller — resolves the actual target case:

1. If `case_number_hint` is set and resolves to a real case the caller is
   authorized for, that's the target; the caller's
   `kefu_staff_case_context` is atomically switched to it.
2. If `case_number_hint` is set but doesn't resolve (wrong number,
   unauthorized, or terminal), the result is `Denied` — never silently
   falling back to the caller's existing bound context, since that would
   mask a real mistake as if it had been ignored.
3. If `case_number_hint` is `None`, the service looks up the caller's
   `kefu_staff_case_context.active_session_id`; if bound and not
   terminal, that's the target (an ordinary unqualified follow-up). If
   unbound or terminal, this turn opens a new case.

**`expected_revision` removed from the caller-supplied contract (Codex
round-70 finding 4).** v3 required the worker to supply it, but the worker
has no authoritative read of case state — a second staff member may
reference a `case_number` for the first time in their own conversation,
with no prior revision to supply at all. The case-turn service already
loads the case and captures the authoritative `case_revision` itself
(§2.5 step 1) before doing anything else — that internally-captured value,
not anything from the caller, is what the CAS `WHERE` clause uses. The
caller only ever supplies *identity* and *intent* (message content,
`case_number` as a lookup key), never state.

**Idempotency ownership, corrected (Codex round-70 finding 1): a claim
lease is concurrency control between workers, not end-to-end idempotency
against a single worker's crash-and-retry.** The case-turn service, not
the worker, owns business-operation idempotency — via `case_turn
.source_message_id`'s uniqueness (§2.5): on a duplicate `msgid`, this
function returns the previously-stored result without re-running
extraction, mutation, or execution, regardless of how many times it's
called for the same message. The worker's lease (§2.6) still matters —
it's what prevents two *concurrent* workers from both calling this
function for the same message at the same time — but it is not, by
itself, a correctness guarantee against a crash-and-reclaim retry. That
guarantee lives here, in the service's own `source_message_id` check.

### 11.2 Claude's artifact producer → Codex's transport

```
Artifact = {
  bytes: bytes,
  filename: str,
  content_type: str,
  artifact_key: str,  # stable idempotency identity — the same logical
                       # document always produces the same key (derived
                       # from request_log_id + doc_type)
}
```

**Error ownership split**: generation errors (the PDF itself failed to
build) never reach the transport layer — they surface as part of
`CaseTurnResult.Error` from §11.1. Delivery errors (upload/send failed
after a valid artifact was produced) are the transport layer's own
concern, tracked via `kefu_outbound_delivery`, and never roll back or
invalidate the already-successful case/request state (same post-commit-
failure discipline as Phase 2).

### 11.3 Case service → Codex's reply/file sender, including closed-window deferred delivery

```
send_reply(
  recipient: {open_kfid: str, external_userid: str},
  delivery_key: str,  # = kefu_outbound_delivery.idempotency_key
  payload: TextPayload(text: str) | FilePayload(artifact: Artifact),
) -> SendResult

SendResult =
    Sent(provider_message_id: str)
  | WindowClosed()    # 48h window isn't open right now
  | QuotaExceeded()   # 5-message cap hit for this window
  | Retryable(error: str)  # transient, safe to retry per attempt_count
  | Failed(error: str)     # terminal, non-retryable
```

**Recipient resolution, corrected (Codex round-70 finding 3): never
derived from `session.opened_by_staff_id`.** `recipient` is resolved from
`kefu_outbound_delivery.recipient_staff_id` (§2.6) — the specific staff
member this delivery is for, immutable at creation time — joined to that
staff member's own `open_kfid`/`external_userid`, not from whoever
happened to open the case. Any authorized staff member may be the one
whose turn actually produced a given response; the recipient is always
whoever that turn was for, not the case's original opener.

**Closed-window handling, resolving Codex's specific catch ("cannot be
sent through the same closed Kefu window"):** when `send_reply` returns
`WindowClosed()`, the case service does **not** attempt to notify the
staff member through Kefu at that moment at all. The
`kefu_outbound_delivery` row is left `status='pending'` with
`last_error='window_closed'`, its `text_content` or
`artifact_request_log_id`/`artifact_doc_type`/`artifact_key` (§2.6)
already durably stored — **surviving a process restart**, which v3's
in-memory-artifact design did not.

**On the staff member's next inbound message** (which reopens a fresh
48h/5-message window), the case-turn service (§11.1), before processing
that new message's own content, looks up pending undelivered
`kefu_outbound_delivery` rows by `recipient_staff_id` matching the
identity that just messaged, and sends the deferred response(s) first —
reconstructing a `file` payload on demand from its stable reference
(Phase 3's PDF generation is logically idempotent, so this reproduces the
exact same document without needing stored bytes). **Quota accounting**:
the reopened window's 5-message budget covers both the deferred delivery
and the fresh reply; if more than 5 items are queued, the oldest pending
deferred delivery is sent first, one per reopened window, the rest
remaining queued — a deliberately simple MVP rule for a rare edge case.

`QuotaExceeded()` and `Retryable()` results feed `kefu_outbound_delivery
.attempt_count`/`next_retry_at` exactly as any other retry; `Failed()`
marks the delivery terminal and surfaces to staff on their next
interaction rather than being silently dropped. Per §2.5's adopted policy,
every `Sent()` result is understood as at-least-once, not exactly-once —
`provider_message_id` is recorded as soon as it's known specifically to
narrow (not eliminate) the window in which a lost response could cause a
duplicate send on retry.

## 12. Work division — true single-writer split

- **Claude Code**: all migrations/models (§2), customer/address backfill,
  grants and Kefu staff authorization including the tagged `role_change`
  dispatch (§2.3), session/case/turn-audit/execution-ledger changes
  (§2.5), warehouse defaults (§3), notifications (§7), PDF handler/
  artifact refactor (§4).
- **Codex**: envelope-neutral crypto + XML adapter (§5), Kefu token/API
  client, callback/sync receiver and worker including lease-based claim
  recovery (§2.6/§6.1), reply/file transport including the closed-window
  deferred-delivery flow (§11.3), and their dedicated tests.
- Each agent reviews the other's diff against the §11 interface contracts
  before either merges. No production file has two writers.

## 13. Remaining implementation/deployment details (not sign-off blockers)

1. `config.SERVER_BASE_URL` — still unconfirmed by the user as the correct
   live Render domain (open since round 56).
2. Exact new callback route path — implementation-time detail.
3. Exact lease duration values (`case_execution.lease_expires_at`,
   `kefu_inbound_message.lease_expires_at`) — implementation-time
   detail, not a design blocker.

## Sign-off

- Claude Code: v7 draft (discussion round 77), restoring the `NOT EXISTS`
  unexpired-claim guard alongside the advisory lock per Codex's round-76
  correction — v6 had incorrectly dropped it — and fixing the
  two-connection test's assertion to match. Every code-level claim
  verified directly against the repository before inclusion across all
  seven drafts.
- Codex: **SIGNED v7 in discussion round 78.** V7 correctly combines the
  per-identity advisory lock with the unexpired-claim guard and corrects the
  real two-connection test expectation. All earlier architecture, durability,
  authorization, case-context, interface, rollout, and acceptance-test findings
  are resolved.

**NOT YET APPROVED by the user. No implementation may begin.**
