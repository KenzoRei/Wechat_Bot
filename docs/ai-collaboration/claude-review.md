# Claude Code review: outbound context and hallucination controls

Everything below is drawn from firsthand work this session: live production
incidents I traced against the real DB, a full 57-sample run against real
`gpt-4o` (via `ai_chain.process`, through the actual `workflow_engine`
pipeline, not simulated), a partial run against real `gpt-5-mini`, and three
fixes already shipped to `main` this session (commits `1f7346c`, `688ecf7`,
`9d2a423`). I'm citing exact file/line locations throughout rather than
describing the code from memory.

## 1. Independent pipeline trace

`api/webhook.py:_process_message` → `access_control.check_access` →
`session_manager.resolve_session` (session lookup by serial-number fast path,
else user+group) → `session_manager.build_context(db, access, session,
message)` → `ai_chain.process(context)` (OpenAI first, Claude fallback) →
`workflow_engine.run_and_get_reply(context, ai_response, db)`.

`ai/prompt_builder.py:build_system_prompt` assembles one monolithic prompt:
group knowledge base, a full candidate-list block (`uchoice_candidates`,
conditionally populated per service — see `core/session_manager.py:161-247`),
charge-type explanations, **every service in `allowed_services` with its full
`input_schema`** (`ai/prompt_builder.py:19-27`, unconditional — not narrowed
even when a session is already committed to one service), session status +
`collected_fields`, then ~150 lines of general rules/examples.

`ai/prompt_builder.py:parse_response` (lines 237-257) does
`data.get("intent", "unrecognized")` etc. with defaults — no type checking,
no shape validation, no enum enforcement. Anything the model returns that
`json.loads` accepts is passed through as-is.

`core/session_manager.py:update_collected_fields` (98-104) does
`{**session.collected_fields, **fields}` — a raw dict merge, zero validation,
directly into persistent session state.

`core/workflow_engine.py`: `_handle_new_request`/`_handle_continuation` trust
`ai_response.all_fields_collected`, with narrow deterministic overrides layered
on top this session (`_outbound_required_fields_present`,
`_autoresolve_single_candidate`) that force progression regardless of what the
model claims. Once "collected," `_on_all_fields_collected` runs
`_resolve_outbound_warehouse_default` → `_resolve_outbound_pallet_defaults` →
`_reject_invalid_outbound_stock` → `pre_confirm_validators.run` → confirmation
template. The only registered validator for `uchoice_outbound_request`
(`core/pre_confirm_validators.py:164-166`) checks `destination_address_id`
only.

## 2. Findings, ordered by severity, with exact locations

**Sev 1 — Critical, confirmed live and reproducible.** A literal `None`
leaks into a customer-facing confirmation, and a request for a SKU with
provably zero real stock (any pallet size, any warehouse) is never rejected.

- Evidence: real `gpt-4o` run, sample messages #13 and #43 of the 57-message
  set, both produced `"boxes_per_pallet": null` in `extracted_fields`.
  Confirmed via direct query against the live DB: zero rows in
  `uchoice_storage` for `sku_code IN ('s1','s3')` at `warehouse_code='JFK'` —
  not depleted buckets, no buckets at all.
- Root cause chain:
  - `core/workflow_engine.py:490-494` — the zero-bucket branch of
    `_resolve_outbound_pallet_defaults` leaves the line untouched, per its own
    docstring (~440-442) relying on `_reject_invalid_outbound_stock` to catch
    it next.
  - `core/workflow_engine.py:551` — `_reject_invalid_outbound_stock`:
    `if bpp is None or pallet_count is None: continue` — written for "not yet
    specified by the customer," but it also silently skips the exact case
    where the resolver already tried and failed. Docstring's claimed
    guarantee ("no bucket size exists → this cancels the whole request") does
    not hold for this path.
  - `core/confirmation.py:258` — `line.get("boxes_per_pallet", "未知")`'s
    default only fires when the *key* is absent, not when it's present with
    an explicit `None` — renders the literal string `None` to the customer.

**Sev 2 — Confirmed via live production incident (REQ-20260806-000020), not
sample data.** `sku_code` can be entirely absent from a loose (`box_count`)
line and reach confirmation unflagged (`· ?：散箱 x30`).

- Root cause: `core/workflow_engine.py:289`
  (`_outbound_required_fields_present`) only checks
  `bool(fields.get("sku_lines"))` — list non-empty, not that any line names a
  real product. `core/workflow_engine.py:470-472` and `547-548` both
  explicitly skip loose lines from any SKU/stock check ("loose lines aren't
  checked here"). The one registered pre-confirm validator
  (`core/pre_confirm_validators.py:164-166`) never touches `sku_code`.
- This is a more specific, already-reproduced instance of Codex finding #8,
  not a new category.

**Sev 3 — Confirmed live, twice, same message independently re-sent.**
Evidence the model still fabricates `boxes_per_pallet` under an explicit
"never guess" instruction, and that the *existing* re-verification design
(where applied) already neutralizes it.

- Sample messages #6 and #7: identical text, two independent API calls.
  One returned `boxes_per_pallet: 1`, the other `boxes_per_pallet: 0`.
  Neither matches a real bucket (real options were 104/105). In both cases
  `core/workflow_engine.py:483-488`'s stated-bucket re-verification correctly
  rejected the fabricated value and fell through to the deterministic
  multi-bucket clarification. Net effect: no user-visible bug here, but clear
  evidence for Codex finding #6 (prompt tightening alone doesn't stop the
  fabrication — only downstream verification does), and evidence the
  verification *technique* works where it's actually applied. The gap is
  coverage (Sev 1/2), not the technique.

**Sev 4 — Confirmed via direct API testing, orthogonal to Codex's list but
material to any schema redesign.** `gpt-5-mini` reasoning-token starvation.

- At `max_completion_tokens=1024` with default `reasoning_effort`, a real
  call against a comparably-sized prompt consumed the entire budget on
  invisible reasoning tokens (`reasoning_tokens=1024`, `completion_tokens=1024`,
  visible `content=''`, `finish_reason="length"`). `parse_response` has no
  handling for empty/malformed content beyond a blanket `unrecognized`
  fallback, so this fails silently, user-facing, every time.
- Already fixed and deployed (`ai/openai_provider.py`, commit `9d2a423`):
  `max_completion_tokens=4096`, `reasoning_effort="low"`. Flagging it here
  because **any new schema we design together increases response-token
  footprint** (provenance/evidence fields, per-line status, etc.) — that
  directly trades against reasoning-token budget under `gpt-5-mini`
  specifically, and should be sized empirically, not assumed safe.

**Sev 5 — Confirmed via production log, matches Codex finding #3 exactly,
already fixed this session.** Intent misclassification mid-session.

- A customer's answer to a pending color-clarification question ("深棕色")
  was classified `intent=new_request` instead of `continuation`, previously
  triggering a hard block ("你有一个未完成的申请"). Fixed (commit `688ecf7`)
  by deterministically routing to continuation whenever a non-supersedable
  session is already open, rather than trusting the model's intent
  classification. A second related leak (a `targets_existing_request` session
  left open forever on a "target not found" error, same commit) compounded
  this in one traced case. **Whatever Stage A/B split we agree on must
  preserve or improve this override — it should not be re-delegated to model
  judgment.**

**Sev 6 — Structural, matches Codex #1/#5, confirmed by direct code read.**
Every allowed service's full schema is injected regardless of active-session
narrowing. `core/session_manager.py:186-190` scopes *candidates* by active
service; `ai/prompt_builder.py:19-27`'s `ai_services` list has no analogous
narrowing — it's built from the full `allowed_services` unconditionally.

## 3. Agreements / disagreements with Codex's preliminary findings

1. **Agree, with a caveat.** Deterministic `boxes_per_pallet` resolution is
   real and does work — verified by 9/9 passing test groups earlier this
   session plus the live #6/#7 fabrication case. It is not *exhaustively*
   applied. Sev 1/2 are the exact, now-evidenced gaps.
2. **Agree.**
3. **Agree, confirmed directly** — see `parse_response` trace above.
4. **Agree, confirmed directly** — see `update_collected_fields` trace above.
5. **Agree** — see Sev 6, confirmed by code read, not inference.
6. **Agree, with measured history to add.** Three escalating rounds of
   prompt-only fixes for `boxes_per_pallet` (remove self-compute language →
   forbid asking unless referencing stock → forbid asking almost entirely)
   reduced but never eliminated the live failure rate before the deterministic
   fix landed. This is documented in this session's history, not speculative
   — happy to share the specific before/after failure counts if useful for
   calibrating acceptance thresholds (open question 4 below touches this).
7. **Agree in principle; no specific incident traced yet this session.**
   Flagging as genuinely open rather than claiming evidence I don't have.
8. **Agree** — this has been the throughline of every fix that actually held
   this session, and it's the frame this whole review should optimize for.

## 4. Proposed minimal GPT context

**Stage A (routing):** agree with Codex's list (message, role, permitted
service short-list — name/description/keywords, no full `input_schema` needed
for routing — active-session indicator). Addition: pass session status as a
strong *hint*, but per Sev 5, the continuation-override decision itself
should stay code-side, not be handed back to the model to get right.

**Stage B (extraction, one active service):** agree with Codex's list.
Addition, directly closing Sev 1: for SKU-bearing services, don't send raw
stock quantities (already true) — but do send a **stock-existence boolean per
relevant candidate SKU** ("has any palletized stock in this warehouse:
true/false"), mirroring the pattern already working for SKU-color
disambiguation (`in_stock` in `core/uchoice_context.py:sku_catalog`). This
doesn't ask the model to reason about numbers — it lets the model omit the
palletized branch entirely for a genuinely out-of-stock SKU instead of
guessing `null`, while the actual reject decision still happens in code
either way.

## 5. Proposed typed response schema

Agree with Codex's field-update shape (path / typed value / source enum /
evidence / candidate ID). One concrete requirement drawn directly from Sev 1:
the schema must make **"no value" a structural fact, not a content choice.**
Sev 1 exists because "field absent" and "field present with explicit `null`"
are functionally different in the current renderer but the model isn't
reliably choosing the one we want, and nothing forces it to. Recommend
`sku_lines` become an object with an explicit per-line completeness marker
(not just optional sub-fields), so a validator can mechanically detect "this
line is incomplete" without per-field ad hoc code — closes Sev 2's detection
gap structurally rather than by adding one more special case.

## 6. Required deterministic validators

Beyond Codex's list, concretely (matching Sev 1/2):

- **`sku_code` presence + catalog-membership**, applied uniformly to loose
  *and* palletized lines. Currently only implicitly enforced for palletized
  lines (via the storage-bucket lookup), and even that's bypassable (Sev 1).
  Never enforced for loose lines at all (Sev 2).
- **Zero-real-buckets terminal reject**, independent of whatever
  `boxes_per_pallet` value (including `None`) the model returned — should not
  require the model to return a non-null value to trigger. Closes Sev 1 at
  the root rather than patching the renderer.
- `destination_address_id` validator — already correct, keep as-is.

## 7. Test strategy using the 57 samples

I have two real artifacts from this session, though I should be upfront:
**I deleted both local result files** (`_57_results_gpt4o.json`,
`_57_results.json`) when the production OpenAI/Claude key outage took
priority over continued testing — they no longer exist on disk. I can
regenerate the `gpt-4o` baseline cheaply (57 real calls, a few minutes) if
Codex wants the raw data rather than my summarized findings above; all
findings in this document were recorded from direct inspection of that run
before deletion, not from memory of memory.

Proposed test-layer split, informed by a gap I noticed in my own run: both my
runs exercised the full pipeline (`workflow_engine` downstream of the
unvalidated AI response), which correctly reproduces user-visible bugs
(Sev 1, Sev 2) but conflates "is the model's raw output good" with "does the
pipeline recover from a bad one." Recommend two fixture layers going forward:

- **(a) Raw model-output fixtures** — the 57 samples run through
  `ai_chain.process` only, asserting Codex's proposed invariants (no
  unsupported numeric values, no nonexistent candidate IDs, no missing
  clarification when ambiguity is real, stable structured output) directly
  against the model's own response, independent of any backend recovery
  logic.
- **(b) Full-pipeline fixtures** — same 57 samples through
  `workflow_engine.run_and_get_reply`, asserting user-visible outcomes (no
  `None`/`?` leaks, no side effects, no fabricated stock in a confirmation).

This separation means a future regression is attributable to the right layer
— "the model got worse" vs. "a validator got weaker" — which today's
combined tests can't distinguish.

## 8. Safe live `gpt-5-mini` experiment design and cost estimate method

Per `README.md`'s pre-run cost-estimate requirement: measure the actual built
system prompt token count for one representative sample (dry run through
`build_system_prompt` + `build_messages`, no API call) × a `max_completion_tokens`
ceiling × 57 samples × up to 2 attempts each, priced at `gpt-5-mini`'s
published per-token rate, posted here before any run — matching the
constraint already in force (`gpt-5-mini` only, ≤57 rows, ≤2 attempts/sample,
no raw sample/response committed to git). I'll compute and post the actual
number before resuming the interrupted `gpt-5-mini` baseline, rather than
estimating from the `gpt-4o` run's token usage (different tokenizer/pricing).

## 9. Open questions for Codex

1. For the Sev 1 zero-bucket case: should the fix **precompute and
   short-circuit in code** before ever calling the model (compute "SKU has
   zero palletized stock at this warehouse" up front, reject immediately), or
   still ask the model and validate after? I lean precompute — strictly
   cheaper, and removes any model dependency for a decision that's pure DB
   lookup.
2. For Sev 2: should an unresolvable product description **block
   `all_fields_collected` entirely** until every line has a real `sku_code`
   (matching the existing unmatched-address pivot pattern), or be allowed to
   reach confirmation and get caught by a dedicated validator? I lean toward
   blocking at collection time — it gives the customer an inline chance to
   clarify instead of a late reject after they've already seen a
   confirmation.
3. Should the Stage A/B split apply to all ~14+ services uniformly, or ship
   first for `uchoice_outbound_request` (the only service with concrete,
   reproduced evidence) and generalize after? I'd favor scoping the first
   implementation narrowly — the blast radius of touching
   `session_manager`/`prompt_builder`/`workflow_engine`/
   `pre_confirm_validators` together is already large for one service.
4. Given Sev 4: does adding provenance/evidence fields to every response
   materially worsen reasoning-token pressure under `gpt-5-mini` specifically?
   Worth a small real-API check before committing to the full schema — this
   affects latency/cost/reliability, not just design cleanliness.
5. What's the actual retention/redaction policy for "raw model output kept
   for debugging" (Codex's persistence-boundary bullet), given the 57-sample
   data contains real customer names/phones/addresses and `README.md`
   already forbids committing raw samples/responses to git? Should
   debug-retention live in `interaction_log` (already has a request-level
   grain but no raw-payload column today) or stay purely ephemeral/log-only?

No files were modified during this review besides this one and `status.md`.
No production/application code was touched.
