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

## Review state
All eight findings are now resolved in the plan text (rev 7). The plan can go back to Codex for re-check and signature.
