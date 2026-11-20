# Customer pickup (客户自提) for outbound requests

**Status:** rev 7, DRAFT, ready for Codex re-check (rev 7: user resolved Q8 and Q9, see D13, D14; rev 6: answers to Codex audit `audit-codex.md`, see `claude-response-rev6.md`; adds "Contracts and chokepoints", Q8 and Q9 for the user; rev 5: PDF mark is now a small black bar in the REMARK area, text only; chat mock-up approved. rev 4: Q6 = yes, sample PDFs generated, banner moved into the title area, consignee-font finding, chat mock-up; rev 3: user answered Q1–Q3, one general `客户自提` row; rev 3: pickup-intent rules, revise-at-confirmation, and a warehouse-facing pickup banner on the PDF). Written by Claude for **Codex audit**. Not signed by either agent, not approved by the user. No code has been changed.
**Branch:** none yet. Proposed: a new branch from `main` (not from `feature/scheduled-outbound-reservation`, which has unrelated unmerged outbound changes).
**Migration:** V42 (confirm it is the next free number before writing it). Must be applied to prod **before** the deploy (migrate-before-deploy lesson).

## Protocol (per the user's standing rules)
- Codex audits this plan and replies in `audit-codex.md` in this folder, append-only, numbered findings. Claude answers each finding: fixed, pushed back with reasons, or escalated.
- Product and user-visible choices are the **user's**. They are in the Decisions table with options. The agents do not settle them between themselves.
- Both agents sign. Any change after signing bumps the revision and needs both signatures again. The user approves before any code is written.

## Goal
For self pickup, record **which customer is coming to the warehouse**, and print the name on the outbound delivery-order PDF. Today a self pickup is one of: a warehouse-retention row (`仓库自提留存`, no person), or the generic walk-in row `散客`.

## Decisions

### Agreed by the user (2026-11-20)
| # | Decision |
|---|---|
| D1 | Seed **one general `客户自提` row** in the address book, `warehouse_code` NULL (valid for any source warehouse). The pickup location is the request's own source warehouse, which is already collected. (rev 2; replaces "one row per warehouse".) |
| D2 | **`散客` is retired.** A walk-in is a `客户自提` with pickup name `散客`. |
| D3 | The customer name is **free text** given by the requester. It is **not** matched against the address book or the customer table. |
| D4 | The LLM context explains the two types (see Prompt). A bare "自提" with neither signal makes the AI **ask which one**, not choose. |
| D5 | The pickup name is shown in the **confirmation summary** and the **kefu renderer**, so the user sees what will print before confirming. |
| D6 | The **modify** and **address-pivot resume** paths carry the name along. |
| D7 | The new rows follow the **same validator rules** as the existing `self_pickup` rows (same-warehouse, fee $0). |
| D8 | The name is printed in the export PDF, in the **consignee block** (user, rev 2). |
| D9 | `散客` is **soft-retired, never deleted**: past requests in production reference it (user, rev 2). |
| D10 | **Classification is by pickup intent, not the literal string `自提`** (user, rev 3). Intent covers wording such as 自提, 自取, 来提货, 来取, 取货, 到仓提货, 客户自己来拿, pickup, will call. No pickup intent means a normal delivery to the matched address. With pickup intent: customer pickup vs retention (D4). |
| D11 | **Pickup intent wins over a named delivery address** (resolves Q4). The named address is not used, and the confirmation says so, e.g. `客户自提 @JFK — 取货人：张三（未使用您提到的地址：X）`. |
| D12 | The user can **revise the type at the confirmation step in either direction** ("这不是送货，是客户自己来提货" / "不是自提，送到 X"). A revision **re-resolves from scratch**: to pickup, clear `destination_address_id` and ask for the name if missing; to delivery, clear the pickup name and run normal address matching; then re-run the validators and show a fresh confirmation. |
| D13 | **`仓库自提留存` means the recipient is Trans World itself**; goods stay physically in place, **stock is deducted as usual**. No inventory change in this feature (user, rev 7, resolves audit F5 / Q8). |
| D14 | The invoice detail sheet shows `客户自提（<name>）` for a customer pickup (user, rev 7, resolves Q9). |

### Proposed by Claude, needs the user's confirmation
| # | Proposal | Why |
|---|---|---|
| P1 | The **LLM does not choose the pickup address row.** Pickup rows are excluded from the semantic match list sent to the LLM for outbound requests. The LLM returns `pickup_type` (`customer` / `retention`) and `pickup_customer_name`; **code** resolves the row: customer pickup → the one general row; retention → the source warehouse's own `仓库自提留存` row. | The validator docstring at `core/pre_confirm_validators.py:619` records a live incident where same-address rows confused the AI. A third near-identical row makes it worse. It also stops a customer's name from matching that customer's own delivery address row. |
| P2 | Add a nullable column **`uchoice_address.pickup_kind`** (`'customer'` / `'retention'`, CHECK constrained). Code never identifies a row by its label text. | The table has no field that tells the rows apart besides label strings. |
| P3 | `散客` is **kept in the table but excluded from the candidate list**, so past requests that reference it still render. It is marked `pickup_kind='legacy'` (CHECK allows `customer` / `retention` / `legacy`), which removes it from the AI list (P1) and keeps its id resolvable. The resolver never returns it. | Confirmed referenced in prod (D9). A separate `legacy` value keeps the resolver's "the one `customer` row" lookup unambiguous. |
| P4 | A prominent **pickup banner on the PDF** for warehouse staff (see PDF section), so staff (a) notice a request wrongly generated as a pickup, and (b) never make a delivery trip for a pickup. Needs the user's call on wording and on whether retention gets its own banner (Q5, Q6). | The user's proposal (rev 3). The PDF title is the generic `PICKUP / DELIVERY ORDER`, so today nothing on the page tells staff which one this is. |

### Resolved by the user (rev 2)
- Q1 (which warehouses): replaced by D1. One general row, no per-warehouse seeding, so LAX/ORD and any future warehouse need nothing.
- Q2 (PDF placement): consignee block (D8).
- Q3 (`散客` referenced in prod): yes (D9).

### Open questions for the user
| # | Question | Options | Claude's recommendation |
|---|---|---|---|
Q4 (pickup request that also names an address): resolved by D11, pickup intent wins.

| # | Question | Options | Claude's recommendation |
|---|---|---|---|
| Q5 | Banner wording and style on the PDF (P4)? | See PDF section | **RESOLVED (user, rev 5):** a black bar with large white Chinese text in the REMARK area, only `客户自提 — 请勿送货` / retention equivalent. Retention wording: **`仓库自提留存`** (user, rev 5). |
| Q6 | Does a **retention** pickup (`仓库自提留存`) also get a banner? | Yes, its own variant; or customer pickup only | **RESOLVED (user, rev 4): yes, retention gets its own banner.** |
| Q7 | Should the warehouse-facing chat message for an outbound (not only the PDF) carry the same pickup flag? | Yes; or PDF only | **RESOLVED (user, rev 5): the mock-up is fine.** Zero extra lines in lists (the flag replaces the existing `→ 目的地` line), one extra line only in the single-request completion confirmation. See "Chat message mock-up" below. |
| Q8 | **(audit F5)** What does `仓库自提留存` mean for stock? Today the seed note says "货物不离开仓", but the code deducts it as a plain outbound (`storage_txns.py:139-149`). | A: keep today's behavior (deducted); B: stock stays available, needing a separate inventory design (storage transactions, completion, invoice) | **RESOLVED (user, rev 7): A, and the premise was wrong.** `仓库自提留存` does **not** mean the stock stays available. It means **the recipient is us, Trans World**: the goods are already in our warehouse so nothing needs to move, hence 留存. **Stock is deducted as usual**, exactly as the code does today. This feature changes no inventory. The seed note "货物不离开仓" only describes the physical fact. |
| Q9 | **(audit F7)** Should the invoice detail sheet show the pickup person? Today it would print `客户自提` with a blank address. | A: show `客户自提（<name>）` in the destination cell; B: leave it as the address row prints | **RESOLVED (user, rev 7): A.** The invoice detail sheet shows `客户自提（<name>）` in the destination cell. One small change in `core/uchoice_invoice_export.py` (`:270-287`), fed by the shared pickup display fact. Retention rows keep printing their own row label. |

## What the code does today (verified read-only)
- `uchoice_address` (V1:237): `address_id, company_name, charge_type, addr, warehouse_code, note, created_by, created_at, destination_warehouse_code`. `charge_type` CHECK allows `self_pickup`. No kind column.
- Seeds: `JFK仓库自提留存`, `DE仓库自提留存` (V2), `NJ仓库自提留存` (V24), all `self_pickup`, `warehouse_code` = their warehouse. `散客`: `self_pickup`, **empty `addr`, `warehouse_code` NULL**, note `客户本人到仓库自行取货，不限仓库`.
- `core/uchoice_context.py:address_candidates` returns every row (optionally scoped by source warehouse, NULL always allowed) to the LLM, which matches the user's description semantically (`ai/prompt_builder.py:12`). On the kefu path the LLM returns `address_match` with candidate IDs, and code asks if ambiguous. On the Smart Robot path the LLM writes `destination_address_id` directly.
- `core/pre_confirm_validators.py:_valid_destination_address_required` (line ~605) rejects a non-UUID, an unknown ID, or a row whose `warehouse_code` differs from the request's (NULL is always allowed).
- `handlers/uchoice/storage_txns.py:139`: the destination row's `destination_warehouse_code` decides `transfer_out` vs `outbound`. The `self_pickup` rows have it NULL, so they are plain `outbound` with fee `CHARGE_TYPE_RATES['self_pickup'] = 0`. A `客户自提` row would behave the same with no change here.
- Outbound request fields are stored in a JSON blob (`destination_address_id`, `sku_lines`, ...). A new key needs **no column**.
- `core/uchoice_delivery_order.py:build_delivery_order_pdf` draws the consignee (company + address), then `Outbound Order #: ...`. It is built from `pdf_stub.py` (two call sites, lines ~106 and ~160), and its output must stay **byte-identical** (`invariant=1`) because deferred sends regenerate it.

## Target behaviour

### Data (V42)
1. `ALTER TABLE uchoice_address ADD COLUMN IF NOT EXISTS pickup_kind VARCHAR(10)` with `CHECK (pickup_kind IN ('customer','retention','legacy'))`.
2. Backfill **by the four stable seed IDs, not by label** (rev 6, audit F6): `bb8c35cb-dda1-404d-9294-682ed3df5659` (JFK), `a25bb922-2340-41ac-8e2d-f0a5192d85d0` (DE), `a1000000-0024-0000-0000-000000000005` (NJ) to `retention`; `5a1cd32c-f2a3-492d-b2c3-360acfff7514` (`散客`) to `legacy` (P3, D9). The migration raises if fewer than four rows match.
2b. Constraints (rev 6): `CHECK (pickup_kind IS NULL OR charge_type = 'self_pickup')`; partial unique indexes: one `customer` row overall, one `retention` row per `warehouse_code`. No new `charge_type` value. Rows with `pickup_kind` are system-managed: `upsert_address` and the admin panel must not edit or delete them (check during implementation).
3. Seed **one** `客户自提` row: `company_name='客户自提'`, `charge_type='self_pickup'`, `addr=''` (same as `散客`; renderers already handle an empty address), `warehouse_code=NULL`, `pickup_kind='customer'`, `destination_warehouse_code=NULL`, a note. Fixed UUID, `ON CONFLICT DO NOTHING`, same style as V24, idempotent on both a fresh and an existing DB.
4. Why one row works: `_valid_destination_address_required` already treats a NULL `warehouse_code` as "any source warehouse", and `storage_txns` treats a NULL `destination_warehouse_code` as a plain outbound with fee $0. Trade-off: the row itself does not say which warehouse. The request's own `warehouse_code` does, and for the PDF the consignee block can print the pickup warehouse from `company_warehouse` (V29) if the user wants it (not required by D8).

### Contracts and chokepoints (rev 6, from audit findings 1–4; supersedes any conflicting text below)
- **Fields live in `extracted_fields`.** `pickup_type` (`customer` / `retention` / `none`; absent = no change this turn, which is the explicit revision signal) and `pickup_customer_name` are collected like any other field on both channels. No new `AIResponse` members and no `parse_response` change (verified: `AIResponse` at `ai/base.py:32-72` and `parse_response` at `ai/prompt_builder.py:518-530` carry only known members). They are added to the outbound service's field list so the sanitizer keeps them; the sanitizer trims the name and caps its length.
- **Sanitizer strips AI-written pickup IDs.** For outbound requests, an AI-supplied `destination_address_id` that resolves to a row with `pickup_kind` set is removed before persistence, on both channels. Only the resolver writes a pickup ID.
- **Turn hook `apply_pickup_turn(previous_fields, merged_fields, extracted)`**, run after the merge and before address matching on both channels, modelled on the existing `apply_address_turn` (Kefu `core/kefu_turn_apply.py:1457`) and `_apply_address_suggestion` (Smart Robot `core/workflow_engine.py:1083`):
  - `pickup_type` customer/retention this turn: clear the old `destination_address_id` (any kind) and, for retention, the name; call the resolver.
  - `pickup_type: none` this turn: clear the pickup ID, name and stored type, so normal address matching runs on the same turn (Kefu's `if not destination_address_id` check at `:1496` then works).
  - Absent: no change, persisted mode stays.
  - It never relies on the AI emitting null, and a merged null never counts as "destination absent".
  - It also runs on the address-pivot resume path (`core/kefu_turn_apply.py:899-907`).
  - After it: readiness, validators, and a fresh confirmation.

### Candidates for the LLM
- Outbound candidate injection filters out `pickup_kind IS NOT NULL`. `address_candidates` for `upsert_address` must stay **unfiltered** (its docstring forbids scoping there), so the filter is an explicit parameter, not a default.

### Resolver (new, small, in core)
- Input: `pickup_type`, source `warehouse_code`, `pickup_customer_name`. Output: `destination_address_id` chosen by code. `customer` → the single row with `pickup_kind='customer'`; `retention` → the row with `pickup_kind='retention'` and `warehouse_code` = the source warehouse. `legacy` rows are never returned.
- Writes `pickup_customer_name` into the request's collected fields (trimmed; internal whitespace normalized; length cap).
- Missing row (no `customer` row, or no retention row for that warehouse): a clean user-facing error. Retention never falls back to another warehouse's row.

### Validators (`core/pre_confirm_validators.py`)
- `pickup_kind='customer'` row and no name: ask for the name. (Walk-in: the user says `散客`.)
- `pickup_kind='retention'` row and a name present: ask which was meant.
- A name present while the destination is not a customer-pickup row: ask, do not silently drop.
- **Vocabulary backstop (rev 6, audit F4; replaces the whole-message regex):** only for a **newly stated** intent this turn.
  - AI newly says pickup (customer/retention) but the message has no pickup vocabulary at all: ask (guards the costly direction, a delivery silently becoming a $0 pickup).
  - AI says nothing about pickup, the session is not in pickup mode, and the message has un-negated pickup vocabulary: ask.
  - Negation (不是自提, 不用自提, 非自提, 不自提) counts as delivery intent and must be paired with `pickup_type: none` from the AI, or the backend asks.
  - Vocabulary inside a span matching a candidate address's company or address text is ignored.
  - A follow-up carrying only a name emits no `pickup_type`, so nothing is checked and the persisted mode stays.
- **Revision (D12):** handled by the transition step of `apply_pickup_turn` (see "Contracts and chokepoints"), not by the validators.
- **Final-combination check (rev 6, audit F2):** at confirmation **and again before execution**, `(pickup_type, destination row's pickup_kind, destination_address_id, pickup_customer_name)` must be consistent: `customer` → the one `customer` row and a name; `retention` → the source warehouse's own retention row and no name; `none`/absent → the destination row has no `pickup_kind`. A request that already references a `legacy` row (`散客`) is allowed only if it existed before this change. Fail closed.
- `_valid_destination_address_required` stays unchanged as the backstop.

### Prompt (`ai/prompt_builder.py`, both Smart Robot and kefu variants)
- Define the two types (rev 7, corrected by the user): **retention (`仓库自提留存`)** = the recipient is **us, Trans World**; the goods are already in our warehouse so nothing is moved (留存, 自留, "留在我们仓库", "给公司自己"). No person involved; stock is still deducted as usual. **Customer pickup (`客户自提`)** = a named outside person or customer comes to the warehouse to collect.
- **Intent first (D10):** decide whether the request is a pickup at all, from intent, with the phrasings listed above as examples. No pickup intent means a normal delivery to the matched address. The prompt must not tell the AI to key on the literal string `自提`.
- **Pickup intent wins over a named address (D11).** Return the pickup fields and ignore the named address for matching, but still report it so the confirmation can say it was not used.
- A bare pickup with neither the customer signal nor the retention signal: ask which one, never choose (D4).
- Both directions of revision at confirmation (D12).
- **Why asymmetric care:** calling a delivery a pickup makes the fee $0 silently (lost revenue); calling a pickup a delivery adds a fee staff will notice. When unsure, ask.
- Return `pickup_type` (`customer` / `retention` / `none`; omit when nothing about pickup changed this turn) and `pickup_customer_name` **inside `extracted_fields`** (rev 6, audit F1), never top-level; never put the name in `destination_address_id` or `address_match`; never match it to an address; never write a pickup row's `destination_address_id`.
- Keep the existing rule that the AI never states a charge type or price.

### Confirmation and renderers
- **One shared pickup display fact (rev 6, audit F7):** a single function builds the label from the **original request fields** (`pickup_type`, `pickup_customer_name`), e.g. `【自提】客户自提：王小明（无需送货）`. Every builder calls it instead of formatting the destination itself:
  - Kefu pending-list arrow: `core/kefu_turn_apply.py:139-150`, fed by `destination_name` (`core/uchoice_context.py:227-237`);
  - the separate pending-candidate `destination` field: `core/uchoice_context.py:286-295,340-354`;
  - batch completion `destination_label`: `core/completion_batch.py:281-289`;
  - confirmation and completion destination sections: `core/confirmation.py:396-411,550-556,677-680,835-841`;
  - `core/kefu_response_renderer.py`, `core/kefu_customer_copy.py`, and Smart Robot result messages.
- **Invoice detail sheet** (`core/uchoice_invoice_export.py:270-287`): today it exports the address row's company and address only, so it would show `客户自提` with a blank address and no person. Open question for the user (Q9).

### Carry-along (D6)
- Modify flow, address-pivot resume (`core/kefu_turn_apply.py`, `core/workflow_engine.py`), and batch completion (`core/completion_batch.py`): the name must survive each, and the guards re-run on a modified request. These are listed as **read first**, because I have not yet traced each path.

### PDF
- `build_delivery_order_pdf(..., pickup_type: str | None = None, pickup_customer_name: str | None = None)` (rev 6, audit F8: the stored `pickup_type` decides the REMARK bar variant and the CJK gating; the name alone is not enough). Both builders in `handlers/uchoice/pdf_stub.py` (`:128-134`, `:185-191`) pass both, read from the request's stored fields. Kefu regenerates from the origin session (`core/kefu_artifact_loader.py:40-60`) and checks bytes against the enqueued hash (`core/kefu_delivery.py:234-273`), so a request with no `pickup_type` key must take the exact existing drawing path. The name prints in the **consignee block** (D8): company, address (empty for this row), then `客户自提：<name>`. Check that an empty address line does not leave a blank gap. With no name the output is byte-identical to today (pinned by a test).
- Both `pdf_stub.py` call sites pass the name from the request fields.
- **Pickup mark in the REMARK area (P4, rev 5: user judged the full-width title banner "too significant", so it is replaced by a small bar in REMARK).**
  - **Sample PDFs (generated from the project's own drawing helpers):** `sample-customer-pickup.pdf` and `sample-retention-pickup.pdf` in this folder. They are mock-ups from a scratch script, not project code.
  - **What it is:** one black bar with large white text, placed in the REMARK area under `Outbound Order #:` and left of the signature column (about 100–975 px wide, 1432–1527 px tall on the 1700x2200 canvas). Text only: `客户自提 — 请勿送货`, or `仓库自提留存 — 请勿送货` for retention. At 26 pt it is the largest text in the lower half of the page. Nothing else on the page changes: no title replacement, no date-label change, no English line, no "contact the office" sentence.
  - **Retention wording:** `仓库自提留存`, the full address-book name, confirmed by the user, so the PDF matches what staff see in the chat list.
  - It is drawn after the static background, on every page of a multi-page order, as a parameter to `_draw_static_background` (or a small helper called beside it).
  - **Consignee font bug found while sampling:** `_draw_wrapped_top` draws the consignee in Helvetica, which has no Chinese glyphs. Verified: today's `JFK仓库自提留存` prints as `JFK■■■■■■` on the PDF. The pickup lines need a CJK-capable consignee drawing (mixed Latin and Chinese). To keep deferred-send regeneration of **old** requests byte-identical, use the CJK drawing only when the request carries the new `pickup_type` key.
  - **Chinese is primary and must contain the word 自提 (user, rev 3).** English is only a smaller secondary line, never the only wording.
  - The pickup name stays only in the consignee block (D8), as `客户自提：<name>`, so the bar itself is short.
  - Everything Chinese uses the registered CJK font (`STSong-Light`). It has no bold face, so emphasis comes from size and the black fill, not bold.
  - The signature block already has `PRINTED LAST NAME`, which staff can match to the pickup name at the counter.
  - **How this serves the two goals:** a black bar with 26 pt text stands out on an otherwise plain page, so a delivery that carries it is visibly wrong (goal a), and a real pickup says in plain words not to ship (goal b). The cost: it sits mid-page in the REMARK area, not at the top, so it is less prominent than the rejected title banner. The chat message flag (`【自提】`) covers the "before opening the PDF" moment.
  - **Byte-identity:** draw the banner only when the **request's own stored fields** say pickup (the new `pickup_type` key), **not** by reading the address row's `pickup_kind`. Otherwise a deferred send that regenerates an **old** request (created before V42, destination `仓库自提留存` or `散客`) would now gain a banner and no longer match its stored hash. Consequence: requests created before this change get no banner. That is the accepted trade-off.
  - Chinese text needs the CJK font (`STSong-Light`, already registered), as the signature labels already use it.

### Chat message mock-up (Q7, short by design)
Today the warehouse sees, per pending outbound, a block of SKU lines, a `→ <destination>` line and a `创建：` line (`tests/kefu/test_kefu_pending_list_layout.py` shows the format; `散客` already renders as `→ 散客`). The pickup flag **reuses the `→` line**, so lists get **no extra lines**:

```
当前有 3 笔待处理的出库申请，请问是哪一条？
【JFK 仓】
1. REQ-20261120-000142
   S2 1500 ft Stretch Wrap ×1箱（散）
   → 【自提】客户自提：王小明（无需送货）
   创建：Lily · 11/20 10:12
2. REQ-20261120-000143
   T2 3-inch Dark Brown Packing Tape ×2箱
   → 【自提】仓库自提留存（货不出仓）
   创建：Lily · 11/20 10:30
3. REQ-20261120-000144
   S2 1500 ft Stretch Wrap ×3箱
   → ABC Trading（123 Main St, Queens, NY 11101）
   创建：Amy · 11/20 11:02
```

For the single-request completion confirmation (where a `⚠️ 此为内部调仓` line already exists as precedent), **one** extra line:

```
关联申请 REQ-20261120-000142（JFK 仓）
S2 1500 ft Stretch Wrap：散箱 x1
目的地：客户自提 — 取货人：王小明
⚠️ 自提：无需送货，货物由客户到仓提取；如非自提请先联系客服
```

Length rules: the pickup marker is a fixed 4-character `【自提】` prefix, the name is truncated to a sane length (e.g. 20 characters), and no explanatory sentence appears in lists, only in the single confirmation.

## Files expected to change
`db/migrations/V42__customer_pickup.sql` (new) · `core/uchoice_context.py` · `core/pre_confirm_validators.py` · a new resolver module · `ai/prompt_builder.py` · `core/confirmation.py` · `core/kefu_response_renderer.py` · `core/kefu_customer_copy.py` · `core/kefu_turn_apply.py` / `core/workflow_engine.py` / `core/completion_batch.py` (carry-along, as the read finds) · `core/uchoice_delivery_order.py` · `handlers/uchoice/pdf_stub.py` · `models/uchoice.py` (new column) · `CHANGELOG.md`, version bump.

## Tests
- Resolver: `customer` returns the one general row for every source warehouse; `retention` returns only the source warehouse's own row; `legacy` (`散客`) is never returned; missing row gives a clean error.
- A historical request that references the `散客` id still renders in confirmation, kefu renderer, PDF and invoice export.
- Validators: all three name rules; non-pickup destinations untouched.
- Candidate list excludes pickup rows for outbound, still includes them for `upsert_address`.
- Prompt rendering contains both definitions and the ask-which rule, in both variants.
- Confirmation summary and kefu rendering show the name.
- PDF: with name, without name (byte-identical to the current output), long name wrapping.
- Modify and address-pivot resume keep the name.
- (rev 6, audit) Parser-to-confirmation on **each channel**: an AI response with `pickup_type`/`pickup_customer_name` in `extracted_fields` reaches `collected_fields`, readiness, validation, confirmation and the PDF.
- (rev 6) Direct-ID injection on both channels: AI writes the general row's ID, a retention row's ID, or `散客`'s ID into `destination_address_id`; each is stripped or rejected. Final-combination check runs at confirmation and again before execution. A historical request that already references `散客` still renders and executes.
- (rev 6) Revision from `pending_confirmation` in both directions on both channels, and from an address-pivot resume: no stale `destination_address_id`, name or type survives; a merged null does not bypass the missing-destination branch.
- (rev 6) Vocabulary backstop: a name-only follow-up ("张三") passes; "不是自提，送到 X" is read as delivery; an address name containing 自提 does not trigger; AI-says-pickup-with-no-vocabulary asks.
- (rev 6) Migration: backfill hits exactly the four seed IDs and raises otherwise; a second `customer` row or a second retention row for one warehouse is rejected by the unique indexes; a non-`self_pickup` row cannot get a `pickup_kind`.
- (rev 6) Renderers: the shared pickup display fact appears in the Kefu pending list, the pending-candidate `destination` field, batch completion, confirmation and completion sections, and Smart Robot result messages.
- (rev 6) PDF: a no-`pickup_type` fixture is byte-identical before and after; a multi-page pickup order has the bar and CJK text on every page.
- Intent: each phrasing in the D10 list classifies as pickup; plain delivery requests do not; a bare pickup asks which type; a pickup naming an address ignores the address and the confirmation says so.
- Vocabulary backstop: AI says pickup with no pickup words in the message, and AI says delivery with pickup words present, both ask instead of proceeding.
- Revision at confirmation, both directions, leaves no stale `destination_address_id` or `pickup_customer_name`.
- PDF mark: the REMARK bar is present for customer pickup and for retention; absent for a normal delivery and for a request with no `pickup_type` key; no-mark output byte-identical to today; the bar and the `客户自提：<name>` line render the Chinese correctly (no missing glyphs) and 自提 appears on the page; the bar repeats on every page of a multi-page order and does not overlap `Outbound Order #:` or the signature column.
- Migration is idempotent (run twice) and the backfill hits exactly the existing rows.
- Full suite, then a live kefu smoke test after deploy.

## Deploy
V42 goes to prod **first**, then the code deploy. The new column is nullable and the seed is additive, so old code keeps working against the migrated DB.

## Risks
- **LLM still mislabels intent** ("ABC DE自提": name or destination?). Mitigation: the ask-which rule, the validators, and the confirmation summary as the final check. Residual risk accepted.
- **Historical `散客` requests** must still render (P3).
- **Prompt growth** on an already long prompt; keep the new text short.
- **Smart Robot path** has no `address_match` safety net. It benefits most from code-side resolution (P1).

## Questions for Codex (please audit these specifically)
1. Is P1 (code resolves the pickup row) sound on **both** the kefu `address_match` contract and the Smart Robot direct-write path? Where could the LLM still write a pickup `destination_address_id` itself, bypassing the resolver?
2. Is the `uchoice_address.warehouse_code` NULL-means-any rule (used by `散客`) relied on anywhere that retiring or re-flagging `散客` would break?
3. `仓库自提留存` is described as "货物不离开仓", but `storage_txns.py:139-149` records it as a plain `outbound`. Is that existing behaviour correct for retention, and does `客户自提` need any different inventory treatment?
4. Does the byte-identical PDF requirement hold when a deferred send regenerates a request created **before** this change (no name key)? Check `pdf_stub.py` and the durable-delivery regeneration path.
5. Are the modify and address-pivot resume paths complete as listed, or is there another place that rebuilds request fields from the old ones and would drop `pickup_customer_name`?
6. Is a nullable `pickup_kind` column with a CHECK the right shape, or would a new `charge_type` value be safer given how `CHARGE_TYPE_RATES` and the CHECK constraint are used?
7. (rev 2) Is one general `客户自提` row with NULL `warehouse_code` and empty `addr` safe everywhere a destination row is read? Check places that assume a non-empty `addr` or a non-NULL `warehouse_code` (invoice export, result messages, kefu copy, `models/kefu.py` notes), and the `legacy` handling of `散客`.
8. (rev 3) Intent classification and the vocabulary backstop: is a regex over the user's message robust enough for both Chinese and English phrasings, and does it handle negation ("不是自提")? Could it mis-fire on an address name that literally contains 自提?
9. (rev 3) Revision at confirmation: which existing mechanism handles a user reply during `pending_confirmation`, and can it clear `destination_address_id` / `pickup_customer_name` and re-run validators without leaving stale fields? Check both channels.
10. (rev 3) PDF banner: confirm the byte-identity argument (banner keyed on the request's own `pickup_type`, not the address row), and check that the REMARK-area bar (rev 5) fits without colliding with `Outbound Order #:`, the signature column, or the legal text, including the longer retention wording.
11. (rev 4) Consignee font: verify that `_draw_wrapped_top` (Helvetica) really prints Chinese as black boxes today, and check the plan's gating (CJK drawing only when the request has `pickup_type`) against the durable-delivery hash for old requests. Is there a safer way to keep old output byte-identical?
12. (rev 4) Chat mock-up: do the pending-list renderers (kefu and Smart Robot) and the single-request completion confirmation all read the destination from one place, so the `【自提】` marker needs changing in one spot, or are there several copies?
13. Anything the plan misses, especially around the invoice export, batch completion, and result messages that show the destination.

## Signatures
- Claude: _pending_
- Codex: _pending_
- User approval: _pending_
