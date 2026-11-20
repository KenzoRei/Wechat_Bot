# Claude response to Codex audit (plan rev 5 → rev 6)

2026-11-20. Answers to the eight findings in `audit-codex.md`. Each finding was checked against the code. Resulting plan changes are in `plan.md` rev 6. Nothing here is signed, and no production code has been written.

| # | Verdict | Summary |
|---|---|---|
| 1 | **Accepted** (verified) | `AIResponse` has no pickup members and `parse_response` drops them (`ai/base.py:32-72`, `ai/prompt_builder.py:518-530`); both turn handlers persist only `extracted_fields`. Fix: carry the fields in `extracted_fields`, no new `AIResponse` members. |
| 2 | **Accepted** (verified) | An AI-supplied `destination_address_id` is merged on both channels, and the validator checks only existence and warehouse scope. Fix: strip AI-supplied IDs that point at a `pickup_kind` row; only the resolver may write them; final-combination check at confirmation and again before execution. |
| 3 | **Accepted** (verified) | Both channels merge over old keys (`{**old, **extracted}` at `core/kefu_turn_apply.py:1451`) and Kefu skips matching when an ID exists (`:1496`). Fix: an explicit transition step, run after the merge and before address matching, that clears stale fields. It does not rely on the AI emitting null. |
| 4 | **Accepted** | A regex over the whole message is wrong. Fix: lexical checks only for a *newly stated* intent this turn, with negation and address-name handling; later turns use the persisted mode. |
| 5 | **Escalated to the user** (Q8) | Whether `仓库自提留存` stock stays available is a product/inventory decision. Recommendation: keep today's behavior (it is deducted as a plain outbound), and do not change inventory in this feature. |
| 6 | **Accepted** | Stable seed IDs, partial unique indexes, `pickup_kind` only on `self_pickup` rows, fail-closed backfill count. |
| 7 | **Accepted; one part escalated** (Q9) | One shared pickup display fact, propagated through every builder listed. Whether the invoice detail sheet shows the pickup person is the user's call. |
| 8 | **Accepted** | Pass `pickup_type` and the name to both PDF call sites; byte-compare fixture for no-key requests; multi-page test. |

## Responses in detail

### 1. Fields and contracts
- **Decision:** the LLM returns `pickup_type` and `pickup_customer_name` **inside `extracted_fields`**. They then flow through the same path as every other collected field on both channels, so no change to `AIResponse`, `parse_response` or the response-format parser is needed beyond the prompt's response-format text.
- `pickup_type` is tri-state when present: `customer`, `retention`, or `none` (an explicit "this is a delivery"). **Absent means no change this turn.** That is the explicit revision signal finding 3 asks for.
- Both fields are added to the outbound service's known field list so the existing sanitizer keeps them, and the sanitizer trims and length-caps the name.
- A turn hook, `apply_pickup_turn(previous_fields, merged_fields, extracted)`, runs after the merge on both channels. It has precedent: `apply_address_turn` (Kefu, `core/kefu_turn_apply.py:1457`) and `_apply_address_suggestion` (Smart Robot, `core/workflow_engine.py:1083`) already do the same job for `upsert_address`.
- **Test:** parser-to-confirmation on each channel (AI response with the two fields, through the merge, to the confirmation text).

### 2. Bypass of code-side resolution
- The sanitizer (run before persistence on both channels) **strips an AI-supplied `destination_address_id` that resolves to a row with `pickup_kind` set**, for new outbound submissions. Only the hook writes a pickup ID, and only from a validated `pickup_type`.
- **Final-combination check:** `(pickup_type, pickup_kind of the destination row, destination_address_id, pickup_customer_name)` must be consistent at confirmation, and again before execution. The rules (all fail closed):
  - `customer`: the one `customer` row, a name present.
  - `retention`: the source warehouse's own retention row, no name.
  - `none` or absent: the destination row has no `pickup_kind`.
  - A request that already references a `legacy` row (`散客`) is allowed **only if it already existed before this change** (historical requests stay readable, per the finding).
- **Test:** direct-ID injection on both channels, for the general row, a retention row, and `散客`.

### 3. Confirmation corrections
- The transition step is part of `apply_pickup_turn`, and it runs **before** Kefu's `if not destination_address_id` check and before Smart Robot's matching:
  - `pickup_type` customer/retention arrives: remove the old `destination_address_id` (any kind), remove a stale `pickup_customer_name` if switching to retention, resolve the new row.
  - `pickup_type: none` arrives: remove the pickup ID, the name and the stored `pickup_type`, so normal address matching runs on the same turn.
  - Then readiness, validators and a fresh confirmation, on both channels.
- It must not depend on the AI emitting null, and a merged null must never count as "destination absent" in a way that skips matching.
- **Address-pivot resume** (`core/kefu_turn_apply.py:899-907`): the hook also runs on the resume path, so a pickup request that was parked for an address upsert keeps or clears its pickup fields consistently.
- **Test:** both directions from `pending_confirmation`, and from an address-pivot resume.

### 4. Vocabulary backstop
Accepted: a regex over each entire message is not a reliable gate. Revised rule (plan Validators section):
- **Check only a newly stated intent this turn.** A follow-up carrying only a name ("张三") emits no `pickup_type`, so nothing is checked and the persisted mode stays.
- **AI newly says pickup (customer/retention) but the message has no pickup vocabulary at all:** ask. This guards the costly direction (a delivery silently becoming a $0 pickup).
- **AI emits nothing about pickup, the session is not in pickup mode, and the message has un-negated pickup vocabulary:** ask.
- **Negation** (不是自提, 不用自提, 非自提, 不自提) counts as delivery intent, not pickup vocabulary. It must be paired with `pickup_type: none` from the AI, or the backend asks.
- **Address names:** vocabulary inside a span that matches a candidate address's company or address text is ignored (so a company literally named with 自提 does not trigger).
- **Test:** "张三" follow-up, "不是自提，送到 X", an address name containing 自提, on both channels.

### 5. Retention inventory meaning (Q8, user decision)
- **Verified:** the seed note says "仓库自留，货物不离开 JFK 仓", but `storage_txns.py:139-149` treats every `self_pickup` row without `destination_warehouse_code` as a plain `outbound` and deducts.
- **Recommendation:** leave inventory exactly as it is today. This feature is about recording who picks up and flagging the paperwork, not about redefining stock. The banner says only 请勿送货, which is true under either meaning.
- **If the user wants "stays available in the source warehouse"**, that is a separate design touching `storage_txns`, completion and the invoice, and should be its own plan.

### 6. Migration
- Backfill by the **four stable seed IDs** (three retention: `bb8c35cb-dda1-404d-9294-682ed3df5659` JFK, `a25bb922-2340-41ac-8e2d-f0a5192d85d0` DE, `a1000000-0024-0000-0000-000000000005` NJ; and `散客` `5a1cd32c-f2a3-492d-b2c3-360acfff7514`), not editable labels. The migration raises if fewer than four rows match.
- `CHECK (pickup_kind IS NULL OR charge_type = 'self_pickup')`.
- Partial unique indexes: one `customer` row overall; one `retention` row per `warehouse_code`.
- `散客` keeps its ID and every historical reference.
- No new `charge_type` value, as the finding says.
- **Added note:** rows with `pickup_kind` are system-managed. The address-editing paths (`upsert_address`, the admin panel) must not edit or delete them, to be checked during implementation.

### 7. Renderer propagation
- **Shared pickup display fact:** one function builds the pickup label from the **original request fields** (`pickup_type`, `pickup_customer_name`), e.g. `【自提】客户自提：王小明（无需送货）`, and every builder calls it instead of formatting the destination itself.
- **Call sites to change:** Kefu pending-list arrow (`core/kefu_turn_apply.py:139-150`, from `destination_name` at `core/uchoice_context.py:227-237`), the separate `destination` field (`core/uchoice_context.py:286-295,340-354`), batch completion `destination_label` (`core/completion_batch.py:281-289`), the confirmation and completion destination sections (`core/confirmation.py:396-411,550-556,677-680,835-841`), and Smart Robot result messages.
- **Invoice detail sheet** (`core/uchoice_invoice_export.py:270-287`) currently exports the address row's company and address only, so it would show `客户自提` with a blank address and no person. This is a **user decision (Q9)**.

### 8. PDF
- Both builders (`handlers/uchoice/pdf_stub.py:128-134,185-191`) pass the stored `pickup_type` as well as the name. Banner choice and CJK gating use `pickup_type` only.
- Old requests with no `pickup_type` key take the **exact existing drawing path**. The test compares output bytes before and after the change for a no-key fixture, not just the look.
- Test on a **multi-page** order as well as one page.

## User resolutions (rev 7, 2026-11-20)
- **F5 / Q8:** the user corrected the premise. `仓库自提留存` means **the recipient is Trans World itself**. The goods are already in our warehouse, so nothing moves (hence 留存), and **stock is deducted as usual**. The current `storage_txns.py:139-149` behavior is therefore correct and stays unchanged. The "stock stays available" reading in the audit and in my first summary was wrong; no separate inventory design is needed. The prompt's definition of retention is corrected to match (plan Prompt section).
- **F7 / Q9:** approved. The invoice detail sheet shows `客户自提（<name>）` for a customer pickup.

## Round 2: responses to findings 9–11 (rev 7 → rev 8)

| # | Verdict | Summary |
|---|---|---|
| 9 | **Accepted** (verified) | `_maybe_pivot_to_add_address` runs at `core/workflow_engine.py:81`, before the handlers, so it can cancel the outbound before `apply_pickup_turn` runs. Fix: gate the pivot on validated pickup intent. |
| 10 | **Accepted** | Grandfather **all** requests created before the cutover, by a durable creation-time marker. Fail closed for new ones. |
| 11 | **Accepted** | Add a bounded, sanitized internal field for the ignored address, with a defined lifecycle and both renderers. |

### 9. Pivot gate (Smart Robot)
- **Verified:** `run_and_get_reply` calls `_maybe_pivot_to_add_address` at `core/workflow_engine.py:81`, before `_handle_new_request` / `_handle_continuation` (`:84-87`). With `pickup_type=customer` plus `unmatched_new_address` in one reply, the pivot closes the session and the request log (`:150-155`) before the hook can act.
- **Kefu is not affected:** its pivot happens inside the turn (`core/kefu_turn_apply.py:1496-1510`), after the merge, and only when no `destination_address_id` exists. With the hook placed before `:1496`, a resolved pickup ID already satisfies that check.
- **Fix:** a small pure function `pickup_intent_this_turn(ai_response, context)` is evaluated **before** the pivot. It returns `customer`, `retention`, `none`, `ask`, or `unchanged`, using the same rules as the revised backstop (AI's `pickup_type` this turn plus the vocabulary check). The pivot returns `False` when the result is `customer`, `retention` or `ask`. Only `none` and `unchanged` let the pivot run as today.
- `ask` also skips the pivot, so the outbound is not abandoned while we are still asking which type was meant.
- **Tests (Smart Robot):** an initial pickup request that names an unmatched delivery address (no pivot, request survives, ignored address recorded); a confirmation revision to pickup that names one; `pickup_type=none` plus an unmatched address still pivots as today.

### 10. Grandfathering in-flight requests
- **Marker:** every outbound request created **after** deploy is stamped with an internal `_pickup_v = 1` at **creation** (the new-request handler on each channel), not in the turn hook. A stamp in the hook would wrongly mark old drafts that continue after deploy. `_` keys cannot come from the AI (existing stripping), so the marker is trustworthy.
- **Rule:**
  - **No `_pickup_v`** (created before the cutover): grandfathered. It is judged by the old rules, with **no** final-combination check, for any destination row including `retention`, `legacy` (`散客`) and rows that now carry a `pickup_kind`. This covers drafts, pending confirmations, and processing requests awaiting warehouse completion, and the pre-execution gate.
  - **`_pickup_v` present:** strict final-combination check, fail closed.
- **Revision of a grandfathered draft:** once the user states a pickup type on a later turn, the hook sets `pickup_type` and the request is judged strictly from then on (the hook stamps `_pickup_v` at that moment, since the request is now in the new model). If the user never states one, it stays on the old path and its PDF takes the exact existing drawing path (byte-identical).
- **Tests:** confirmation and completion of an old retention request, an old `散客` request, and an old ordinary delivery, each created before the migration and handled after; a new request with no type and a retention destination is rejected.

### 11. The ignored address (D11)
- **Field:** an internal `_pickup_ignored_address` in `collected_fields`, written only by the hook, so the AI cannot write it. Its value is a single bounded string (80 characters max, whitespace normalized, newlines and markup stripped).
- **Source** (deterministic, not AI free text): (a) the sanitized `company_name`/`addr` from `unmatched_new_address`, or (b) the label of the first `address_match.candidate_ids` entry looked up in the database. If neither exists, the field is not set and the confirmation shows no "not used" note, since nothing is known to report.
- **Lifecycle:**
  - Any turn that (re)states `pickup_type` customer/retention **recomputes** the field from that turn's address evidence. No address evidence clears it. That is how "the later revision removes that address".
  - `pickup_type: none` clears it, along with the pickup fields (the named address is then matched normally).
  - A turn with `pickup_type` absent leaves it unchanged.
  - Prompt rule: while in pickup mode, any message correcting the pickup or its address must re-emit `pickup_type`.
- **Rendering:** the shared pickup display fact gets an optional second part, `（未使用您提到的地址：X）`, used **only in confirmation builders** on both channels. Lists, the PDF and the invoice never print it.
- **Tests:** a pickup request that names a delivery address shows the note on both channels; a later revision that drops the address shows a confirmation without it; a switch to delivery clears it.

## Round 3: responses to findings 12–13 (rev 8 → rev 9)

| # | Verdict | Summary |
|---|---|---|
| 12 | **Accepted** | `unchanged` is the normal output for a name-only continuation of a pickup, so the pivot gate must use the **effective** mode (this turn's `pickup_type`, else the persisted one). |
| 13 | **Accepted** | The ignored-address note must come from validated evidence only: a unique match inside the turn-local candidate list, or the sanitized unmatched text. Ambiguous, fabricated or out-of-list IDs produce no note. |

### 12. Effective mode for the pivot gate
- `pickup_intent_this_turn(ai_response, context)` now returns the **effective mode after this turn**: this turn's `pickup_type` if present and validated, else the session's persisted `pickup_type` (read through `_get_session`, which the pivot already uses), else none. Outcomes: `customer`, `retention`, `none`, `ask`.
- The pivot returns `False` when the effective mode is `customer`, `retention` or `ask`. It runs as today only when the effective mode is `none` (no pickup now and none persisted), including a brand-new request with no pickup signal.
- A name-only continuation of an existing pickup that also carries `unmatched_new_address` therefore keeps the request. The address text, if valid evidence (see 13), updates `_pickup_ignored_address`.
- **Tests (Smart Robot):** a multi-turn pickup where turn 2 is name-only with an unmatched-address signal (no pivot, request and pickup fields intact); the rev 8 initial-turn and revision cases; a delivery request with `unmatched_new_address` still pivots.

### 13. Validated evidence for the ignored address
- **Only two sources are allowed:**
  1. the **sanitized text** of `unmatched_new_address` (`company_name` / `addr`), which is the user's own description and already passes the existing sanitizer;
  2. the **label of a unique, valid match**: on Kefu, the single candidate ID that passes the existing turn-local validation (`core/kefu_response_renderer.py:97-119`, against the exact candidate list injected this turn), with status `matched`. On Smart Robot, an AI-supplied `destination_address_id` counts only if it is in this turn's candidate list and is not a pickup row.
- **Everything else yields no note:** an `ambiguous` result, any ID not in the turn-local list, a fabricated ID, a pickup row's ID. The pickup decision itself is unchanged (pickup still wins, D11); we simply do not claim an address the user did not clearly name. No extra question is asked for this, since the note is informational.
- The hook **reuses** the validation helper and never applies the match: it supplies the pickup destination ID itself, so Kefu's own address branch stays skipped (`core/kefu_turn_apply.py:1496-1503`).
- **Tests:** pickup intent with an ambiguous `address_match` (no note), with a fabricated candidate ID (no note), with an ID outside the turn-local list (no note), with a unique valid match (note shows its label), on Kefu; and the Smart Robot equivalents.

## User decision (rev 10, 2026-11-20): drop the ignored-address note
- The user chose to drop the note from D11. The confirmation no longer says that a named delivery address was not used.
- **Effect on earlier answers:** the `_pickup_ignored_address` field, its lifecycle, its evidence validation, and its confirmation rendering (round 2 item 11, round 3 item 13) are **removed**. Finding 11 is resolved by removal and finding 13 is moot. The sentence in item 12 saying the address text "updates `_pickup_ignored_address`" is void.
- **Kept:** the pivot gate on the effective mode (finding 12), the grandfather marker `_pickup_v` (finding 10), and the rule that a pickup request never takes an AI-written or ambiguous address ID as its destination (the sanitizer strips pickup-row IDs, and the hook supplies the pickup destination itself).
- **Replacement test:** see plan Tests, "(rev 10, replaces the F11 and F13 tests)".

## Review state
All eight findings are now resolved in the plan text (rev 7). The plan can go back to Codex for re-check and signature.
