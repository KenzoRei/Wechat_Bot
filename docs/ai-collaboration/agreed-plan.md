# Agreed plan

Status: **APPROVED by the user.** Final agreement verified by both agents;
user approval given in chat with Claude Code (confirming all three phases
together, alongside Phase 2's SKU-substitution policy decision — see
`systemic-validation-addendum.md` §4). Implementation may now begin, per the
corrected §7: the pre-implementation test phase runs first and must record
expected baseline failures for Sev 1/2 before any application-code change
lands.

Synthesized from `discussion.md` responses 1–6 and `claude-review.md`. Where
this document and `discussion.md` differ in wording, `discussion.md` is the
source of record for how each point was reached; this file is the flattened,
implementation-ready summary.

## 1. Root causes

Two confirmed, reproduced bugs in the current `uchoice_outbound_request`
pipeline (both traced to exact file/line locations and confirmed against live
data — see `claude-review.md` Sev 1/2, verified independently by Codex against
`HEAD` at commits `1f7346c`, `688ecf7`, `9d2a423`):

1. **Zero-bucket `None` leak.** `_resolve_outbound_pallet_defaults`
   (`core/workflow_engine.py:490-494`) deliberately leaves a palletized line
   unresolved when the SKU has zero real storage buckets in the target
   warehouse, on the documented assumption that
   `_reject_invalid_outbound_stock` catches it next. That function's guard
   (`core/workflow_engine.py:551`, `if bpp is None or pallet_count is None:
   continue`) was written for "not yet specified by the customer" and
   silently skips this case too. The confirmation renderer
   (`core/confirmation.py:258`) then renders the literal string `"None"` to
   the customer, and the request is never rejected despite the SKU having no
   fulfillable stock of any pallet size.
2. **Missing/invalid `sku_code` reaches confirmation.**
   `_outbound_required_fields_present` (`core/workflow_engine.py:289`) only
   checks that `sku_lines` is non-empty, not that any line names a real
   product. `core/workflow_engine.py:470-472` and `547-548` both explicitly
   exclude loose (`box_count`) lines from any SKU/stock check. The one
   registered pre-confirm validator for this service
   (`core/pre_confirm_validators.py:164-166`) validates only
   `destination_address_id`. Reproduced live in production
   (`REQ-20260806-000020`, confirmed directly against the DB).

**Structural root cause behind both:** the model's raw output is trusted as
workflow state without a validation boundary — `parse_response`
(`ai/prompt_builder.py:237-257`) performs no type/shape checking, and
`update_collected_fields` (`core/session_manager.py:98-104`) merges it
directly into persistent session state. Three escalating rounds of
prompt-only tightening for `boxes_per_pallet` (documented in this session's
history) reduced but never eliminated the live failure rate — confirming
prompt reliability alone is not a sufficient control, only a validated
persistence boundary is.

## 2. Context sent to GPT

**Stage A (routing a new message)** — used only when no active session
already claims the turn (see §7 ordering):

- Current message, user role, active-session indicator.
- Permitted-service short list: `name`/`description`/`keywords` only — not
  full `input_schema` (routing doesn't need it).
- No stock quantities, no full conversation history.

**Stage B (extraction for one active/selected service)**:

- The one active service's contract.
- Server-validated `collected_fields` (evidence only, never
  `execution_plan` — see §4).
- Server-computed missing fields.
- Relevant authoritative candidates for this service only (not every service
  the role can reach).
- Relevant group presets.
- Last assistant question + current user message (not necessarily full
  history — open item for later services, out of scope for this
  outbound-scoped rollout).

**Explicitly excluded from context, for `uchoice_outbound_request`
specifically:**

- Raw storage-bucket quantities (already removed this session, V5 migration
  — kept as-is).
- Any stock-existence decision signal beyond the existing coarse `in_stock`
  boolean, which remains solely a SKU-disambiguation tie-break, never a
  stock-availability authorization signal. GPT proposes a SKU candidate from
  language; code alone validates membership, queries authoritative stock, and
  decides reject/resolve/ask.
- `execution_plan` (server-only, never serialized into the prompt — see §4).

**Call-count strategy** (Codex's proposal, accepted without modification):

- Active session → one extraction call for the locked service.
- New request with a deterministic, unambiguous outbound trigger → route in
  code, then one outbound extraction call.
- Ambiguous new request → one compact routing call; a second extraction call
  only after a service is selected or the user clarifies.
- This does not force two calls for every new request.

## 3. Typed response contract

Discriminated union per outbound line, `kind` required:

```json
{ "kind": "palletized", "sku_code": "s2", "pallet_count": 1, "stated_boxes_per_pallet": null }
```
```json
{ "kind": "loose", "sku_code": "s2", "box_count": 30 }
```

Rules:

- `sku_code`, `kind`, and the kind-specific quantity are schema-required.
- `stated_boxes_per_pallet` is **required-but-nullable**: the key must always
  be present. A *missing key* is a schema/parse failure (reject, do not
  merge); an explicit `null` means "the customer did not state it." These are
  not the same failure mode and must not be conflated anywhere downstream.
- The model never writes a resolved/operational value — see §4 for where
  that lives instead.

**Provenance** (compact form, required only for risky values — numeric
quantities and candidate IDs):

```json
{ "field": "sku_lines[0].pallet_count", "source": "current_message", "evidence": "一版" }
```

Whether broader provenance is worth its token cost is an open empirical
question, not a design decision — see §5/§6 (compact vs. full-provenance
evaluation).

## 4. Server-side validation and persistence rules

**Structural separation** (Codex's strengthening of Claude's original
proposal, adopted as-is):

- `collected_fields`: validated customer evidence only. May be sent to GPT.
  `build_system_prompt` serializes this object today
  (`ai/prompt_builder.py:29`) — nothing server-only may ever be nested inside
  it, at any depth, because that serialization has no per-key exclusion
  logic.
- `execution_plan`: a separate, server-owned structure. Never included in
  model context or the model's response schema. Holds `resolved_boxes_per_pallet`
  and any other code-computed operational value.
- Confirmation rendering consumes `(collected_fields, execution_plan)`
  jointly.
- Execution **revalidates/recomputes `execution_plan` transactionally**
  at confirm-time, not just at first-build time — stock can change between
  confirmation display and the user's confirm reply (mirrors the existing
  accepted pattern in `_reject_invalid_outbound_stock`, which already
  re-queries rather than trusting a stale number).
- First implementation: `execution_plan` may be ephemeral (recomputed each
  relevant turn), not a new persisted DB column. Persisting it needs its own
  justification (e.g. reproducing an exact proposed allocation across turns)
  and, if added later, belongs in a separate server-owned column/table,
  still revalidated before mutation.

**Required deterministic validators** (all uniform across loose *and*
palletized lines — today's implicit-only-for-palletized coverage is exactly
Sev 1/2's gap):

- `sku_code` presence + catalog-membership.
- Zero-real-bucket terminal check, independent of whatever
  `stated_boxes_per_pallet` value (including explicit `null`) the model
  returned.
- `destination_address_id` real-candidate check (already correct today,
  keep as-is).
- Unknown field-path rejection (a fabricated key must not silently merge).
- Candidate-ID membership check against the *exact* candidate list sent that
  turn (not stale history).
- Type/range validation (`pallet_count`/`box_count` positive integers).

**Mixed valid/invalid line handling (T10, resolved across responses 3–5):**

- Validate each line independently.
- **T10a** (one valid + one recoverable-invalid line): preserve the valid
  line in `collected_fields` so the user isn't asked to repeat it; return a
  targeted clarification naming only the unresolved line; do not build
  `execution_plan` yet.
- **T10b** (user corrects the bad line): merge only the corrected line.
  The previously-accepted line is **not exempted from revalidation** —
  "preserved without re-prompting" is not "trusted forever." Once the full
  request is complete, code revalidates every line against the current
  authoritative catalog/stock before building `execution_plan`.
- **T10c** (any line has a *terminal* failure — e.g. zero real buckets for
  that SKU, where no customer answer can make inventory exist): this is
  **not** a clarification case. Close/cancel the request before confirmation;
  return one deterministic combined diagnostic naming the terminal problem
  and any other invalid line; do not ask the user to clarify inside the
  now-cancelled session; tell them to submit a new request once the
  underlying data/stock condition is fixed. No partial `execution_plan`, no
  side effect. A single combined clarification is only appropriate when
  *every* invalid line is a user-resolvable ambiguity — if any line is
  terminal, the combined response is a rejection, not a question.

## 5. Regression and live-model evaluation design

**Two independent fixture layers** (do not conflate — this session's own
gpt-4o/gpt-5-mini runs mixed them, which is a gap this design corrects):

- **(a) Raw-model layer**: the 57 real samples through the model call only,
  graded against labels, independent of any backend recovery logic.
- **(b) Full-pipeline layer**: the same samples through
  `workflow_engine.run_and_get_reply`, asserting user-visible outcomes.

**Offline unit tests** (T1–T13 + T10a/b/c, no live API calls):

- T1–T2: zero-bucket SKU, stated value `null` and stated value fabricated
  (e.g. `999`) — both must reject identically, proving rejection depends on
  real bucket existence, not null-ness.
- T3–T6: exactly-one-bucket auto-resolve; 2+-bucket clarification with no
  stated value; 2+-bucket trusted-match with sufficient stock; 2+-bucket
  stated-but-insufficient-stock falls through to clarification (these four
  restate the 9 test groups already passing from this session's earlier
  `_resolve_outbound_pallet_defaults` work — reusable, not written from
  scratch).
- T7–T9: missing `sku_code` on a loose line; invalid/uncataloged `sku_code`
  on a loose line; missing `sku_code` on a palletized line (tested
  independently, not assumed symmetric with the loose case).
- T10a/b/c: as specified in §4.
- T11–T13: unknown field-path rejection; stale/foreign candidate-ID
  rejection; non-positive-integer quantity rejection.
- Universal regression guard across every test above: the literal substring
  `"None"` must never appear in any generated user-facing message.

**Labeling format for the 57 real samples** (acceptable-sets + hard
invariants, not single golden outputs — most of the 57 are not
single-turn-completable, confirmed directly from this session's earlier
`gpt-4o` run):

```json
{
  "id": 1,
  "message": "...",
  "single_turn_completable": false,
  "labels": {
    "expected_intent": "new_request",
    "expected_service": "uchoice_outbound_request",
    "sku_lines": [
      {
        "acceptable_sku_codes": ["t2"],
        "kind": "palletized",
        "pallet_count": 1,
        "stated_boxes_per_pallet_must_be": null,
        "evidence_spans": ["一版棕色胶带"],
        "notes": "color ambiguous but t3 has zero stock -- in_stock tie-break resolves to t2 alone"
      }
    ],
    "destination_candidates": ["<address_id>"]
  },
  "raw_model_expectations": { "...": "what the model alone should propose" },
  "pipeline_expectations": { "...": "what the user should ultimately see" },
  "must_not": ["fabricate_boxes_per_pallet", "confirm_without_valid_sku_code", "render_none_literal"],
  "fixture_catalog_version": "<pin to a specific SKU/stock snapshot, not the live DB>"
}
```

Labels (`acceptable_sku_codes`, ambiguity notes) require domain-owner review
before becoming ground truth, and must record a fixture/catalog version
rather than silently depending on live, mutable DB state.

## 6. Acceptance thresholds

Two separate gate sets — raw-model quality is measured and reported, but the
**pipeline/validator layer is the actual production safety boundary**
(Codex's correction, accepted: prompt reliability alone cannot be a
production gate, since the measured `gpt-4o` evidence already shows explicit
anti-fabrication prompting doesn't guarantee zero bad proposals — the design
goal is making such proposals structurally harmless, not eliminating them).

**Raw-model layer** (57 samples):

| Metric | Gate |
|---|---|
| Empty output, first attempt | ≤ 1/57 |
| Schema-valid, first attempt | ≥ 56/57 (98.25%) |
| Schema-valid after one bounded repair retry | 57/57 |
| Unsupported numeric/candidate proposals | reported, not gated |
| Acceptable-set accuracy (unambiguous labeled fields) | ≥ 95% |
| Correct clarification behavior on labeled-ambiguous cases | ≥ 95% |

**Validator / full-pipeline layer** (the hard safety boundary):

| Invariant | Gate |
|---|---|
| Unknown field persisted | 0 |
| Missing/invalid SKU reaches confirmation | 0 |
| Unsupported candidate ID reaches confirmation | 0 |
| Unvalidated numeric value reaches confirmation/execution | 0 |
| Literal `None`/internal placeholder reaches a user-visible message | 0 |
| Invalid request produces an operational side effect | 0 |
| Continuation rerouted as new request while a protected session is active | 0 |
| T1–T13 (+T10a/b/c) all pass | required |

**Compact vs. full-provenance decision rule**: adopt full-provenance only if
it measurably reduces validator-layer violations or improves acceptable-set
accuracy over compact; otherwise default to compact (reasoning-token budget
concern — see §8).

## 7. Staged implementation plan

Scoped to `uchoice_outbound_request` only for this first pass — reusable
validator primitives, but no cross-service migration in the same change.
Generalize to other services only after this fixture suite passes and
production behavior is stable.

**Deterministic routing order (hard invariant, explicitly confirmed both
directions — must never be violated by any future change):**

1. Resolve access and active session.
2. If a non-supersedable session exists, force the active-service
   continuation path before examining any new-request shortcut or keyword.
3. Only when no active session exists may an unambiguous deterministic
   new-request router select outbound.
4. Otherwise, use the compact model router.

No heuristic or keyword router may run ahead of step 2 — this is the
regression invariant protecting this session's already-shipped Sev-5 fix
(commit `688ecf7`).

**Staged order, corrected** (Codex's response-6 catch: the original draft
placed production changes *before* the regression harness meant to define
and prove them, and blurred the authorization boundary — the user has
authorized test-suite work now, but not application changes. Fixed below;
"implement" is used deliberately instead of "ship" for the implementation
phase — passing each local stage does not itself authorize commit, push, or
deployment):

**Pre-implementation phase — allowed now, before any production approval:**

1. Create the isolated test package: `conftest.py` isolation fixture,
   `private_data/` ignore rule, the synthetic/redacted grader fixture, and
   the fail-closed operational-client/transport guards (§7 below).
2. Encode T1–T13 and T10a/b/c against **current, unmodified** behavior.
3. Run the suite and record the **expected baseline failures** for Sev 1/2
   (these tests are supposed to fail today — that's what proves they're
   testing the real bug, not a strawman) — tests for already-correct
   behavior (e.g. the existing 9 `_resolve_outbound_pallet_defaults` test
   groups) must pass now, unchanged.
4. Complete and domain-review the 57-sample labels.

**Implementation phase — begins only after explicit, separate user approval
of this plan:**

5. Implement the typed outbound contract and the validated persistence
   boundary.
6. Implement the `collected_fields`/server-only `execution_plan` separation.
7. Implement the uniform (loose + palletized) `sku_code`/quantity/candidate/
   stock validators and T10a/b/c handling.
8. Run the complete offline suite until every validator-layer hard gate in
   §6 passes — the Sev 1/2 tests recorded as expected-failures in step 3
   must now pass.
9. Only then: run the authorized, budget-capped live `gpt-5-mini`
   compact-schema evaluation (§7 cost ceiling below). Full-provenance
   evaluation, if pursued, requires its own fresh cost measurement (§8) —
   the $1.32 ceiling is for the compact schema only.
10. Review live-evaluation results against the raw-model gates (§6). Do
    **not** deploy automatically on a passing result.
11. Present the diff, full test evidence, live-evaluation usage/cost, and
    remaining risks (§8) to the user for a **separate deployment decision** —
    passing this plan's gates authorizes nothing beyond itself.

**Test infrastructure** (`tests/uchoice_outbound/`):

```
tests/uchoice_outbound/
  conftest.py                    # isolation fixture (see below)
  fixtures/
    sample_format.md             # documents the labeling schema, no real data, committed
    synthetic_labels.json        # small redacted/synthetic fixture, committed, exercises the grader in normal CI
  private_data/                  # GITIGNORED as a whole directory
    labeled_samples.json         # the real 57 labels with real addresses/phones
    live_run_results/            # raw live-eval output
  test_sev1_zero_bucket.py       # T1-T6
  test_sev2_missing_sku.py       # T7-T10c
  test_validator_boundaries.py   # T11-T13
  test_raw_model_layer.py        # schema-valid/empty-output/acceptable-set rates against stored/replayed output
  test_live_gpt5mini.py          # live-API run; skipped unless an explicit env flag is set
```

`.gitignore` gains one new entry: `tests/uchoice_outbound/private_data/`
(a directory, not per-filename entries) — this is test infrastructure, not
a production behavior change, but is listed here explicitly for user review
per Codex's request.

**Test isolation** (layered — a single-module monkeypatch is confirmed
insufficient, since production code imports client functions by value):

1. Patch the public client exports: `clients.wechat_client.{send_message,
   send_group_webhook_message, send_group_webhook_file}`,
   `clients.oms_client.{query_outbound_order, create_work_order}`,
   `clients.yidida_client.create_label`.
2. Patch every already-bound operational alias reachable by the tested
   workflow — confirmed by direct import-statement inspection, not assumed:
   `core.workflow_engine._send_raw`, `handlers.reply_wechat.send_message`,
   `handlers.label.base.create_label`,
   `handlers.oms_create_workorder.{query_outbound_order, create_work_order}`,
   plus WeChat aliases in `jobs/` and `api/webhook.py`.
3. Transport-level kill switch: monkeypatch
   `requests.sessions.Session.request` to raise — all three current
   operational clients route through `requests.post`, confirmed by direct
   grep of all three client files.
4. During the authorized live `gpt-5-mini` test specifically, keep the
   `requests` kill switch active; the OpenAI SDK uses `httpx`, so the
   permitted call still succeeds while WeChat/YiDiDa/OMS transports stay
   blocked.
5. Assert the blocked-call counter is exactly zero after each test, rather
   than merely relying on the absence of a raised exception (a call that
   silently no-ops wouldn't be caught by exception-absence alone).
6. The fixture fails closed (raises at setup) if any listed alias can't be
   found/patched — protects against a misspelled target silently weakening
   isolation.
7. Documented limitation, not solved by this design: if an operational
   client later migrates from `requests` to another transport (e.g. `httpx`),
   this fixture must be updated — record covered transports/modules in the
   test file's own docstring so this doesn't silently rot.

**Request-count/budget guard** for `test_live_gpt5mini.py`:

- Skipped by default; runs only with an explicit environment flag set.
- Validates the target model name exactly (`gpt-5-mini`, per README — no
  silent drift to another model).
- Hard-caps total calls at `len(labeled_samples) × 2` via a counter fixture
  that raises once exceeded.
- Refuses to run if `private_data/labeled_samples.json` is missing or
  contains more than 57 entries.
- Enforces the recorded dollar ceiling below from observed API `usage`
  fields (not the pre-run estimate) as a running total.

**Cost ceiling for the compact-schema live evaluation** (measured, not
estimated — confirmed by both agents):

- Real prompt token counts measured locally via `tiktoken` (`o200k_base`)
  across all 57 real messages against the *current* (large) context shape:
  13,383–13,447 tokens/prompt, 764,147 total for one pass.
- Current `gpt-5-mini` pricing (fetched live from OpenAI's pricing page):
  $0.25/1M input, $2.00/1M output.
- `max_completion_tokens=4096` ceiling (shipped, `ai/openai_provider.py`,
  commit `9d2a423`).
- **Worst case** (every sample uses both allowed attempts, every attempt
  hits the output ceiling): input 1,532,958 tokens ≈ $0.383; output 466,944
  tokens ≈ $0.934. **Total ceiling ≈ $1.32.**
- **Realistic case** (1 attempt/sample, ~1,000 output tokens/call average):
  ≈ $0.31.
- Qualifications (both agents noted): `o200k_base` is a local estimate, not
  authoritative billed usage — actual billed tokens must be recorded from API
  responses during the run. The 13.4K-token measurement uses the *current*
  large context, not the final proposed compact context — making $1.32
  conservative for the compact design once implemented, but the compact and
  full-provenance variants must each be freshly measured from their actual
  built prompts before their own runs; prompt-cache discounts are ignored
  (also conservative).
- **The compact-schema live evaluation is authorized up to this $1.32
  ceiling, but must not start until:**
  1. The label format is finalized and reviewed by a domain owner.
  2. `private_data/` exists and is gitignored.
  3. The layered isolation fixture (above) is implemented and its
     fail-closed behavior verified.
  4. The request-count/budget guard is implemented in the test runner.

## 8. Remaining disagreements or risks

No material design disagreement remains between the two agents as of
response 5. Open items carried forward, not full disagreements:

- **T10c's rejection wording** is specified in principle (§4) but the exact
  user-facing Chinese copy hasn't been drafted or reviewed — implementation
  detail, not a design gap.
- **Reasoning-token pressure from added provenance** (Sev 4-adjacent):
  whether full-provenance materially worsens `gpt-5-mini` reasoning-token
  consumption relative to compact provenance is an open empirical question,
  not yet measured — the plan explicitly defers full-provenance evaluation
  behind a fresh, separate cost measurement rather than assuming it's safe.
- **Raw-output retention policy**: agreed to keep live-evaluation raw output
  ephemeral/gitignored, and to *not* add full raw model payloads to
  `interaction_log` in this fix — but the longer-term production retention/
  redaction policy for structured validation events (accepted/rejected field
  path, reason code, model, prompt/schema version, token counts, correlation
  ID — explicitly *without* raw addresses/phones/evidence text) is deferred
  as "a separate retention/redaction decision," not resolved here.
- **Transport-isolation fragility**: explicitly documented as
  necessary-for-today, not permanent — a future transport migration
  (`requests` → `httpx` or similar) in any operational client silently
  weakens the kill switch unless the test docstring's coverage note is kept
  current. This is a known, accepted maintenance risk, not a blocker.
- **Cross-service generalization** is intentionally out of scope for this
  plan (§7) — explicitly deferred, not rejected.

## Sign-off

- Codex: signed off in `discussion.md` response 5 ("No material design
  disagreement remains after the T10 and isolation corrections. Claude Code
  may now draft `agreed-plan.md`.")
- Claude Code: this document constitutes sign-off; no further challenges
  raised against response 5's content, which was independently verified
  (client export names and `requests.post` usage confirmed by direct
  file inspection before being incorporated into §7 above).

**APPROVED by the user.** Implementation may begin, per §7's fixture-first
sequencing.
