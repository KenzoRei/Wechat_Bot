# Phase 3: outbound pickup/delivery instruction PDF timing

Status: **Signed by both agents (Claude Code's round-19 revision; Codex's
round-20 final verification and sign-off) and APPROVED by the user**, given
in chat with Claude Code alongside approval of Phase 1 and Phase 2 (recorded
in `discussion.md` round 21). Every underlying technical claim was
independently verified against the repository before incorporation. This
document is
**additive** to, and coordinates
with, `agreed-plan.md` (Phase 1) and `systemic-validation-addendum.md`
(Phase 2) — it does not modify either document's own sections, though
Phase 2's §3b/§5 were updated (round 15) to reflect this phase's existence
so the two don't silently contradict each other on where the outbound
instruction PDF is generated. Nothing in this document authorizes any
application-code change; the same two-stage authorization rule applies as
Phase 1/2 (pre-implementation test work allowed now; implementation begins
only after the user separately approves this specific phase — approving
Phase 1 or Phase 2 does not imply approval of Phase 3, and vice versa).

**Explicit packaging note**: Codex's round-14 response proposed this as "a
small Phase 1 amendment (or companion change-order document)." The user
subsequently and explicitly directed, in chat with Claude Code, that this
become **its own separate Phase 3** that both agents add to their plan —
that instruction is what this document implements. The substance of the
design is Codex's; only the packaging (a standalone, separately-signed
phase rather than a Phase 1 amendment) reflects the user's direction over
Codex's original suggestion. Flagging this plainly so Codex isn't surprised
by the packaging choice diverging from what it proposed.

## 1. The problem

Not a hallucination/validation-boundary bug like Phase 1/2 — a
**workflow-step-placement error**, found because the user corrected an
earlier assumption of their own about when PDF generation should happen.

**Requirement** (explicitly stated by the user, independently confirmed to
both agents): the customer-facing pickup/delivery **instruction** PDF must
be generated when the customer's `uchoice_outbound_request` is created and
confirmed — not later, when a warehouseman confirms
`confirm_outbound_completion`.

**Current implementation is the reverse**, confirmed by direct repository
inspection (round 13, re-confirmed round 14):

- `uchoice_outbound_request`'s workflow: `record_uchoice_request → reply_wechat`
  — no PDF step at all.
- `confirm_outbound_completion`'s workflow: storage mutation →
  `generate_pdf_stub({doc_type: delivery_confirmation})` →
  `complete_existing_request` (webhook) → `reply_wechat`.
- The current PDF handler reads `_uchoice_target.original_fields` and
  `context.result.fulfillment_lines` — both **completion-time** structures,
  confirmed by Codex's round-14 direct read. This means even relocating the
  *step* isn't enough on its own — the handler's data source needs to change
  too (see §3).

Net effect today: a customer never receives a pickup/delivery-instruction
document at the point they actually need it (when the request is created),
and the existing PDF only fires once a warehouseman has already completed
the shipment — by which point an instruction document is moot, not useful.

## 2. Relationship to Phase 2's transaction-boundary work — a real dependency, not just a file overlap

Touches an overlapping file (`confirm_outbound_completion`'s workflow step
sequence, and `core/workflow_engine.py`'s step-loop machinery), and — per
Codex's round-16 verification — **depends on Phase 2's fix, not merely
adjacent to it.**

`uchoice_outbound_request`'s request log is created `pending` before
customer confirmation, and `mark_processing` commits immediately before
`_run_workflow_steps` runs — so a new request-time PDF step technically
*can* run post-commit of that early state transition. But
`_execute_workflow_and_finish`'s current exception handling
(`core/workflow_engine.py:782-797`) wraps the **entire** step sequence,
including the final `reply_wechat`, in one `try`/broad `except Exception`
that unconditionally calls `mark_failed` on any exception — confirmed by
direct read. A PDF-generation step added to this same workflow would be
caught by that same broad handler: if it (or an unrelated later step, like
`reply_wechat` itself) throws, the request gets marked `failed` even though
the actual, valid outbound request was already correctly created. That
directly violates §3.5's acceptance rule below (PDF/delivery failure must
leave the request `processing`, not `failed`).

**Ordering decision (resolved round 18/19, no longer open):** implement
Phase 2's DB-phase/post-commit-side-effect engine split first, or land it in
the same coordinated change, and extend that split to cover
`uchoice_outbound_request`'s workflow (today out of Phase 2's originally-
named scope, since that workflow has no storage mutation — Phase 2's split
was designed around inventory-mutating services, but the same mechanism
applies cleanly here). **Do not build a second, Phase-3-only exception
framework** unless the user later explicitly requires Phase 3 to ship before
Phase 2 — a duplicated, temporary failure-semantics implementation in the
same engine is worse than waiting for the shared one. Both documents remain
independently **approvable** as plans by the user, but full Phase 3
**implementation** is gated on this shared-engine prerequisite from Phase 2
— recorded here explicitly rather than silently assumed.

## 3. Design (Codex's round-14 proposal, adopted in full)

1. After the customer's outbound request is validated, confirmed, and
   successfully created/persisted, generate the pickup/delivery instruction
   PDF from the request's validated `collected_fields` plus server-owned
   execution/instruction data (not from completion-time structures like
   `fulfillment_lines`, which don't exist yet at this point in the
   lifecycle and shouldn't be the data source even once they do).
2. Run PDF creation/delivery **post-commit** (same phase-2 discipline as
   Phase 2 §3b): failure must not cancel or relabel the already-created,
   successful outbound request. Record and retry/surface the PDF failure
   separately, as a delivery-failure concern, not an operation failure.
3. **Remove** the pickup/delivery instruction PDF step from
   `confirm_outbound_completion`. Warehouse completion records actual
   fulfillment and mutates inventory — it does not (re-)create the
   customer's instruction document, which by that point has already been
   generated and (presumably) delivered at request-creation time.
4. If the business separately wants an actual-fulfillment/completion
   document (e.g. a receipt confirming what was physically shipped, as
   opposed to what was originally instructed), that's a **separate
   artifact with its own contract** — not the instruction PDF silently kept
   at the old timing under a different justification. Out of scope for this
   phase unless the user asks for it explicitly.
5. **Logical idempotency, not literal one-time generation** (corrected per
   Codex's round-16 verification of `core/download_tokens.py:17-25`): that
   module mints a brand-new random `secrets.token_urlsafe(32)` on every
   `create_token` call, stored in an in-memory dict with a 1-hour TTL and no
   request-keyed dedup — confirmed by direct read. It cannot support a
   literal "generated exactly once" claim while also allowing retries or a
   fresh download link after expiry, and the store doesn't survive a process
   restart. Phase 3 instead requires:
   - **one logical instruction-document version per outbound request**,
     keyed by the request's serial/log ID and built from an immutable
     validated request snapshot (not re-derived from mutable session state
     that could change between retries);
   - an **idempotent handler**: retrying PDF generation must not create a
     second *business* document or change its content, even though it may
     produce a new token/access link;
   - re-rendering identical bytes and/or issuing a new short-lived access
     token after the old one expires is explicitly allowed and does **not**
     count as a second logical document;
   - no claim of durable exactly-once *delivery* — that would need a
     persisted artifact/outbox design, which doesn't exist today and is out
     of scope for this phase.
   - **every content-bearing input must itself be stable and
     request-derived — including the displayed document/instruction date**
     (added round 19, per Codex's round-18 finding). Confirmed by direct
     read: the current renderer passes `datetime.now(timezone.utc).date()`
     as `delivery_date` (`handlers/uchoice/pdf_stub.py:65`), so a retry
     after midnight would silently change document content even though the
     underlying validated request snapshot hasn't changed — a real
     violation of the idempotency rule just stated above it. Use an
     explicitly persisted lifecycle timestamp instead — default
     `RequestLog.created_at` (confirmed present, `models/request_log.py:23`)
     — or a future validated requested-service date if the business adds
     one, never the wall clock at generation/retry time.
6. Tests must assert **timing, source-of-truth, and logical idempotency**,
   not just "a PDF gets generated somewhere" and not literal call-count:
   - exactly one logical instruction-document version exists per outbound
     request, generated after request creation/confirmation — not a literal
     "the function was called exactly once" assertion;
   - uses the request's own validated/requested data, not
     `fulfillment_lines` or any other completion-time structure;
   - no PDF generation occurs during `confirm_outbound_completion`;
   - retrying the PDF step doesn't change the document's content or count as
     a second logical document;
   - **a retry that crosses midnight (or any other wall-clock boundary)
     produces identical logical content** — the displayed date must come
     from the persisted request timestamp, not `datetime.now()` at render
     time;
   - a PDF-generation (or, per §2, a `reply_wechat`) failure leaves the
     outbound request in its normal successful (`processing`, per Phase 1's
     two-step awaits-completion semantics) state — not cancelled, not marked
     `failed`.

## 4. Scope boundary with `confirm_inbound_completion`

Unaffected by this phase. `confirm_inbound_completion`'s own
`generate_pdf_stub` step (the inbound *receiving* document — a different
document, for a different party, at a different point in the lifecycle) is
untouched unless the user separately raises the same question for inbound.
Not assumed symmetric — the user's stated requirement was specifically
about the outbound pickup/delivery instruction.

## 5. Work division and production-file scope

Given the file overlap with Phase 2 (§2 above), and that Phase 2 already
assigns `core/workflow_engine.py` and the mutation/workflow-step machinery
to Claude Code as single writer: **Claude Code** takes this phase's
implementation too, for the same file-ownership reason Phase 2 used
(avoiding two agents editing the same workflow/step-sequencing code).
Codex's role here is design verification and test review, matching its role
elsewhere in Phase 2 — not proposing a split ownership on a single-service,
tightly-scoped change like this one.

**Full production-file surface**, named explicitly per Codex's round-16
point so this isn't mistaken for an engine-only edit:

- A **new forward migration** to move/add/remove `workflow_step` rows for
  `uchoice_outbound_request` and `confirm_outbound_completion` — never edit
  `db/migrations/V2__seed_catalog.sql` in place, per this project's
  established squash/forward-only migration convention.
- `handlers/uchoice/pdf_stub.py` — its current outbound code path depends on
  `_uchoice_target.original_fields` and `context.result.fulfillment_lines`
  (both completion-time structures, confirmed round 13/14); this needs a
  request-creation-time code path reading the request's own validated
  `collected_fields` instead.
- `core/workflow_engine.py` — the phase/error-handling change from §2,
  whether supplied by this phase directly (§2's alternative) or inherited
  from Phase 2's split (§2's recommended path).
- Delivery-order input-mapping/template code — only if the new request-time
  data source requires different field mapping than the completion-time
  path currently uses; not assumed necessary until tests prove it.
- Dedicated Phase 3 tests per §6.

Open to Codex's counter-proposal on ownership if it disagrees with the
single-writer reasoning, but it's a narrower, lower-risk allocation question
than Phase 2's, so defaulting to "whoever already owns the adjacent
workflow-engine code" rather than opening a new negotiation.

## 6. Test strategy

Same fixture-first discipline as Phase 1/2: encode the §3.6 tests against
**current, unmodified** behavior first — they should fail today (no PDF at
outbound-request creation; a PDF does get generated at completion time; the
existing exception handling doesn't distinguish delivery failures from
operation failures), proving they test the real gap before any
implementation exists. Implementation begins only after the user's separate
approval of this phase, and — per §2 — after its ordering dependency with
Phase 2 is resolved one way or the other.

## 7. Remaining disagreements or risks

None as of round 19. Every item raised across rounds 14-18 has been
resolved and incorporated, each underlying technical claim independently
verified against the repository before acceptance (never taken on faith):
the packaging (Phase 3 vs. Phase 1 amendment), ownership, inbound-scope
boundary, logical-idempotency wording, the Phase 2 ordering dependency (now
resolved: extend Phase 2's engine split, no separate Phase-3-only exception
framework), the full production-file scope, and the stable-request-date
requirement (`RequestLog.created_at` over wall-clock `datetime.now()`).

## Sign-off

- Claude Code: this document is my draft (round 15), adopting Codex's
  round-14 design in full, repackaged as a standalone phase per explicit
  user direction; revised (round 17) per Codex's round-16 refinements;
  revised again (round 19) to record the round-18 ordering decision and add
  the stable-date idempotency requirement — both round-18 technical claims
  (`handlers/uchoice/pdf_stub.py:65`'s wall-clock date;
  `models/request_log.py:23`'s `created_at`) independently verified before
  incorporation.
- Codex: **signed, round 20** — accepted the packaging, ownership, and
  inbound-scope boundary in round 16; confirmed logical-idempotency and full
  file-scope refinements and selected the Phase-2-first/shared-engine path in
  round 18; finally verified the resolved ordering and stable request-derived
  date/cross-midnight retry requirements in round 20.

**APPROVED by the user.** Implementation may begin, sequenced behind
Phase 2's shared-engine prerequisite per §2.
