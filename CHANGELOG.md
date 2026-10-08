# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning started with `v1.0.0` (tagged retroactively at the pre-existing
baseline); prior history predates tagging and isn't broken out by version
here.

## [1.8.0] - 2026-10-08

**Apply migration V41 immediately before this deploy, in the same window.**
(With V41 but the old code, Kefu would ask for the 柜号 by its raw field
name and then ignore it.)

### Changed
- **Inbound requests record a container number (柜号) instead of 需要拆包.**
  Both channels ask `柜号是多少？（没有柜号请回复「无」）`; the request
  confirmation always shows `柜号：…` or `柜号：无`. A value not in the
  standard format (4 letters + 7 digits) is questioned once and accepted if
  confirmed. Spaces, dashes and lowercase are tidied automatically.
- **The container unpacking fee (拆柜费) is set by the warehouse at
  receipt,** replacing the flat $300.
  - Request with a 柜号: the warehouse must give the amount before
    confirming ($0 allowed).
  - Request without one (including every request made before this
    release): the confirmation warns `此入库无柜号，拆柜费将为 $0`; 确认
    gives $0, or the warehouse replies with an amount (optionally with a
    柜号) to charge it.
  - Amounts are checked against what was actually typed: a mistyped
    `45.555` is rejected even if the AI read it as 45.55; numbers from a
    柜号, a request ID or a quantity can never become the fee. Fees above
    $10,000 are questioned once.
  - A 是/否 answer to one of these questions is handled in code before the
    AI and never confirms the request or receipt.
- **Batch receipt confirmation** leaves out requests with a 柜号 (they need
  a fee, so they're confirmed one at a time); the others show
  `柜号：无，拆柜费 $0`.
- **Invoice:** the Inbound sheet has a Container # column; the fee is
  "Container unpacking fee" / 拆柜费 everywhere.

### Fixed
- A Kefu case awaiting confirmation that fails its checks after a
  correction now leaves the confirmation, so a later 确认 can't execute it.

## [1.7.0] - 2026-10-08

**Migration V40 must be applied to production before this deploy.**

### Added
- **Cancel several outbound or inbound requests at once (Kefu).** `取消出库`
  / `取消入库` shows the usual numbered list, now ending with
  `回复编号取消，可多选（如「1 3」或「全部」）`.
  - Picking two or more shows a summary of exactly those requests (goods,
    destination, creator) with `取消后无法恢复`. `确认` cancels them all; a
    number reply such as `①③` cancels only those; `放弃` does nothing.
  - All-or-nothing: if a selected request can no longer be cancelled when
    you confirm (e.g. the warehouse just completed it), nothing is
    cancelled and the summary is shown again without it.
  - The result lists each request with its creator, e.g. `REQ-…108（Harry）`.
  - Picking just one keeps the single-cancel flow. Each original requester
    is notified as before when someone else cancels their request.
  - New services `cancel_inbound_request_batch` / `cancel_outbound_request_batch`
    (V40), granted to the same roles as the single cancel services. Kefu
    only; Smart Robot is unchanged.

### Fixed
- **Cancellation notices.** The "your request was cancelled" notice to a
  Kefu requester raised `idempotency_key_collision` on every send (the
  request ID was passed as text, not a UUID). The row was still saved, so
  notices arrived, but every cancel logged a false error. Fixed.
- **A failed cancellation notice can no longer undo the cancellation.**
  Each notice now runs in its own savepoint; before, a database error while
  preparing a notice could block the whole Kefu turn's commit. Applies to
  single and batch cancels.

## [1.6.1] - 2026-10-07

### Added
- U-Choice SKU **TL1 4x6 inch Label** (`tl1`), via migration V39. The bot
  reads the SKU catalog live, so customers can name it right away; outbound
  needs stock to be inbounded first.

## [1.6.0] - 2026-10-02

### Changed
- **Outbound request to an unsaved destination (Kefu): the request now
  resumes after the address is added.** Previously the outbound request was
  cancelled and had to be sent again once the address was saved.
  - The outbound draft is put on hold, keeping its serial number, and the
    new-address step opens already filled in: company, address, warehouse,
    and a suggested charge type. When nothing is missing it goes straight to
    the address confirmation.
  - After 确认, the address is saved and the outbound continues in the same
    reply with the new address, normally straight to its own confirmation.
  - 取消 during the address step cancels the outbound too. If the address is
    saved under a different warehouse, or the outbound has expired, the
    address is still saved and the reply says the outbound must be sent again.
  - A message sent to the on-hold outbound (by its case number, or by another
    staff member) is handled on the address step, with a note saying so.
  - Smart Robot still cancels the outbound and asks for it to be sent again.
- **Suggested charge type for a new address (Kefu and Smart Robot).** The AI
  estimates the drive time from the warehouse to the recipient; the bot maps
  it to the tier (under 5 min 短途配送, 5–20 配送, over 20 卡车转仓) and shows
  it in the address confirmation, e.g.
  `配送（$45） — 预计车程约 12 分钟（JFK 仓出发，系统估算，如有误请直接说明）`.
  A charge type the user states (including 自提) always wins. If an estimate
  isn't possible, the charge type is asked for as before. An existing
  address being updated never gets a suggestion.
- **New addresses are stored in one standard format.** The AI returns its
  reading of the address in parts (street, unit, city, state, ZIP); the bot
  checks each part and builds the address itself, e.g.
  `182-08 149th Avenue, Springfield Gardens, NY 11413` from a messy input.
  The built address is what the confirmation shows and what is saved. Only
  such a checked address gets a suggested charge type; if a part is missing
  (e.g. no ZIP), the AI's own text is kept and the charge type is asked. On Smart Robot the
  tier, price and minutes only ever appear in the code-built confirmation,
  never in the AI's own reply.
- **The warehouse carries over** from the outbound request into the
  new-address step (both channels), instead of being asked again.

### Fixed
- A Kefu case awaiting confirmation that loses a required field through a
  correction now goes back to collecting it, instead of letting the next
  确认 execute an incomplete case.

## [1.5.1] - 2026-10-01

### Fixed
- **Invoice Outbound sheet showed `x?` for batch-confirmed requests.** A
  batch completion ("确认出库" with several requests) stored only the source
  picks as its shipped lines, without the pallet or box count the invoice
  reads. It now stores the request's original lines, with their counts, the
  same as a single confirmation; picks are still kept in `source_picks`.
  Batch rows already completed this way take their quantities from the
  original request, which a batch always ships at.

### Changed
- Invoice SKU Lines (Outbound and Inbound sheets) show the unit: `x1托` for
  pallets, `散箱x1` for loose boxes. Previously both read as a bare `x1`.

## [1.5.0] - 2026-10-01

### Changed
- **Kefu pending-request list redesigned** (confirm outbound/inbound, both
  cancel lists, and the batch "请问要确认哪几笔" question):
  - Grouped by warehouse (【JFK 仓】), oldest first within each.
  - One block per request: one line per SKU with units spelled out (`×1托`,
    `×2箱（散）`), `→ destination`, and `创建：name · 9/28 14:05`.
  - Times are New York time. The creator is the Kefu staff member or group
    member who submitted it; with no name on record it is left out.
  - The street address is shown only when two listed requests go to the same
    destination name; no more empty `散客（）`.
  - Header "当前有 N 笔待处理的…"; footer "回复编号确认，可多选（如「1 3」或「全部」）".
  - Reply numbers always match what was shown: the display order and the
    fallback used for "全部确认出库" (no list shown) come from one ordering.
  - If a long list would exceed Kefu's 2048-byte limit, each request's details
    fold onto one line, then drop; numbers never change.
- **Kefu replies are plain text.** WeCom Kefu doesn't render markdown, so
  `**bold**` showed as literal asterisks in confirmations, footers, section
  headings and prompts. Kefu now converts it: a bold line becomes 【标题】, an
  inline bold word 「确认」. Applied to every Kefu reply as sent and as stored
  (case record and conversation history). Smart Robot keeps its markdown.

### Dependencies
- Added `tzdata`, so New York time works even without system time-zone data.

## [1.4.1] - 2026-10-01

### Fixed
- `SERVER_BASE_URL` defaulted to the old `wechat-bot-atse` test service, which
  no longer exists; the default is now the production service
  (`https://wechat-bot-5c5w.onrender.com`). Download links (labels, PDFs,
  invoice workbooks) and the admin export link read `config.SERVER_BASE_URL`
  directly instead of repeating the stale URL as their own fallback. Set
  `SERVER_BASE_URL` explicitly in every deployment, as before.
- Removed a `SyntaxWarning` at startup (`api/admin_panel.py`, an unescaped
  `\d` in the embedded admin-panel JavaScript). The served page is unchanged.

### Documentation
- `docs/operations/admin-api.md` now points at the production base URL.

### Tests
- Updated two stale assertions from the customer-identity role change
  (`93e952f`): the error now reads "customer-identity role", and a Kefu staff
  member can hold the customer role, so the test now checks that assigning it
  without a billing customer is rejected. The full suite passes.

## [1.4.0] - 2026-10-01

### Added
- **Inventory sheet in the 费用报告 workbook.** One row per SKU with opening
  and closing pallets for the period, plus Inbound / Outbound / Other Net
  Change (pallets), and a Closing Detail column listing the closing balance
  per pallet size, one per line, in the same `N 托 @ B/托` format as 库存查询.
  - Balances come from the stock-change history, so past months work with
    no backfill (production check: history matches current stock exactly).
  - A loose-box pick doesn't change the pallet count; it shows in Closing
    Detail as a smaller pallet. Other Net Change covers transfers between
    warehouses, internal moves/repackaging, adjustments and recounts.
  - The Summary sheet and the chat reply add opening → closing pallet totals
    (库存（托）：期初 X → 期末 Y).
- **Combined 费用报告 for several warehouses, and the warehouse is now
  optional.** The bot only asks for the month range.
  - With no warehouse named, or "全部仓库", the report covers every warehouse
    the requester may see: all three for admin and accountant, the assigned
    ones for a warehouseman. A warehouseman with no warehouses assigned is
    refused.
  - Naming one or more warehouses gives one combined invoice. The reply
    always lists the warehouses covered and, for several, a 各仓合计 line each.
  - The Summary sheet is a table of charges by warehouse with a Total column
    and Total row (formulas). Filename: `invoice_DE-JFK-NJ_<start>_<end>.xlsx`.
  - Admin export (`/admin/invoices/export`, `/export-link`) accepts
    `warehouse_code=JFK`, `JFK,DE` or `all`. Migration `V38`.
- New stock-change type `opening` (shown as 期初), counted as starting stock in
  the Inventory sheet. Each warehouse's go-live 库存盘点 was relabelled to it,
  so September opens with that stock (DE 42 / JFK 51 / NJ 23 pallets).
  Migration `V36`; one-time script `scripts/relabel_golive_opening.py`.
- `scripts/check_storage_history.py`: read-only release check. Q1 compares
  the stock-change history with current stock (must be 0 rows); Q4 is a
  deploy gate; Q5 a post-deploy check.

### Changed
- Invoice detail sheets: every row starts with a **Warehouse** column; the
  sheets are named `Outbound` / `Inbound` (were `outbound` / `inbound`); each
  has a frozen header row and filters.
- The chat reply and the attached workbook are built from one calculation:
  fee rows and stock balances are each read once, so they can't disagree.

### Fixed
- An unknown warehouse code in 费用报告 (e.g. "all", "JFK,DE" in the old
  single field) silently produced an all-zero invoice for admin/accountant;
  it is now rejected naming the valid codes. An unreadable or mixed selection
  ("全部" plus a code, a blank value) is rejected too, not treated as "all".
- Kefu could fail to deliver a current-month 费用报告 or 库存历史 file: it rebuilt
  the file from live data on every send attempt, and any change in between
  failed the hash check. The exact bytes are now stored when the file is
  queued and reused on every attempt, kept 30 days unless still pending.
  A duplicate message for a purged file is skipped, never rebuilt. Migration
  `V37`.
- The invoice Summary sheet's blank spacer rows were never written.

### Deployment notes
- `V36` and `V37` were applied to production before this release, and the
  go-live relabel has been run.
- Right before deploying, `check_storage_history.py` must show Q1 = 0 and
  Q4 = 0 (no Kefu invoice files queued by the old code still pending).
- Apply **`V38` after the deploy**, not before: the previous code with `V38`
  would run 费用报告 without a warehouse and return an empty invoice. The new
  code works with either schema.
- After deploying, Q5 lists any invoice file that failed because the old
  code queued it during the switchover; that person just requests it again.

## [1.3.1] - 2026-09-25

### Documentation
- Recorded the implemented OpenAI-first, Claude-fallback provider order in
  ADR-011 and superseded the earlier Claude-primary decision.
- Documented Kefu's service allowlist, migration-runner dry-run side effect,
  and test-environment assumptions.
- Clarified the voice-alert settings: an unset or empty variable uses the
  default threshold, and an explicit `0` disables it. (An earlier edit in
  this release had wrongly stated that `0` restores the default.)

## [1.3.0] - 2026-09-25

### Added
- **Kefu voice input.** A voice message from an authorized staff member is
  downloaded from WeCom, checked as AMR (60-second cap, measured before any
  paid call), converted to WAV, transcribed with OpenAI `gpt-transcribe`
  (the current SKU codes are sent as vocabulary hints), and then handled
  exactly like typed text.
  - Every voice reply starts with what was heard: "🎤 识别内容：…".
  - **Voice can start, narrow or cancel a request, but can't confirm one.**
    A voice "确认" gets "语音不能直接确认。请核对上方摘要后，输入文字「确认」。",
    and voice numbers on a batch summary only narrow it. Admin/system
    commands (registration, purge) are typed-only.
  - Temporary transcription failures retry after 20 s / 60 s / 3 min, with
    a one-time "正在重试" notice. That staff member's later messages wait,
    so everything is processed in order. Permanent failures (expired
    audio, over 60 s, nothing recognized) get a fixed reply; the AI never
    runs on an empty voice message.
  - Senders who aren't registered, active staff are never transcribed.
  - The transcript is saved once, so retries reuse it (no double charge, no
    different wording). The audio itself is not stored.
  - A daily usage alert (log-only, never blocks) fires at 500 clips or
    60 audio-minutes per UTC day; configurable, and `0` disables.
  - New optional settings: `OPENAI_TRANSCRIBE_MODEL`,
    `OPENAI_TRANSCRIBE_API_KEY`, `VOICE_ALERT_DAILY_CLIPS`,
    `VOICE_ALERT_DAILY_MINUTES` (see `docs/reference/configuration.md`).
    New dependency: `imageio-ffmpeg`. Migration `V35`.
- **Kefu batch completion confirmation.** Warehouse staff can confirm several
  processing inbound/outbound requests in one turn ("全部确认出库",
  "确认 086 和 091", or a number reply like "13" to the list).
  - Code resolves the selection against the numbered list the user saw,
    capped at 9 per batch.
  - It runs all-or-nothing, at original quantities. It locks every request,
    then the full stock scope, and re-checks the pallet picks against what
    the summary showed before changing stock.
  - A request cancelled in the meantime, or changed picks, re-shows the
    summary instead of executing.
  - Only an exact "确认" or a pure number reply executes; the AI alone never
    does.
  - New services `confirm_inbound_completion_batch` /
    `confirm_outbound_completion_batch`, Kefu only, granted to the same roles
    as single completion. Migration `V33`.
- Kefu: image, file, video, location and other non-text messages now get
  "暂不支持该消息类型，请发送文字或语音。" instead of silently running an AI turn
  on an empty message. Replies to messages that never became a case use a
  new delivery target (migration `V34`).

### Changed
- The outbound completion confirmation now shows the destination company and
  address.
- The "申请已完成" group-chat push is no longer sent for requests created
  through Kefu. Their submitters already get Kefu's own completion notice.
  Requests created in the group chat still get the push.
- Kefu inbound messages are now processed strictly in order per staff member
  (only the oldest outstanding message is claimable, and only when due).

### Fixed
- Deploy failure: SQLAlchemy is pinned to `>=2.0,<2.1`. The unpinned
  requirement pulled in 2.1.0, which switched `postgresql://` URLs to the
  psycopg 3 driver the app doesn't install ("No module named 'psycopg'").

### Upgrade notes
- **Apply `V35` before deploying this release.** The new Kefu claim query
  reads its columns, so new code on the old schema would stop all Kefu
  message processing. `V33`–`V35` only add things the old code ignores, so
  applying them first is safe. See `docs/operations/migrations.md`.
- The OpenAI key or project must allow `gpt-transcribe` for voice input, or
  set `OPENAI_TRANSCRIBE_API_KEY`.

## [1.2.2] - 2026-09-24

### Fixed
- Kefu: picking one of several pending requests no longer fails. When
  "确认发货" (or any confirm/cancel of an existing inbound/outbound request)
  matched more than one candidate, the bot listed them and asked
  "请问是哪一条？" — but the same turn also cancelled the case, so the
  staff's answer ("第二条", or the pasted serial) arrived with no open case
  and got "抱歉，没能理解您需要哪项服务". The case now stays open until the
  staff picks one. Its placeholder request log is still cleaned up if the
  answer is then rejected (unknown or non-processing serial), instead of
  being left behind as a stray `pending` row.

## [1.2.1] - 2026-09-15

### Fixed
- Admin panel, Users & Groups tab: renamed from "Staff & Groups" (the table
  already mixes staff and customer-identity registrants, not just staff).
  Tables wider than their card now scroll horizontally on their own instead
  of squeezing columns and wrapping long cells onto multiple lines, which
  had been pushing the Save/Delete buttons out of reach — those now stay
  pinned to the visible edge (`position: sticky`) at any scroll position.
- Admin panel, Customers tab: replaced the two raw-JSON dict inputs
  (`ydd_channel_id`, `rate_multiplier`) with one labeled input per carrier
  (FedEx/UPS), and collapsed the always-expanded 4-row credentials block
  into a compact "N/4 set" summary with a Manage toggle — both were causing
  uneven, hard-to-scan row heights.
- Admin panel, Warehouses tab: one Save button per row instead of one per
  field (nine per row), which also fixes the column misalignment the extra
  buttons caused.
- Documentation: corrected several inaccuracies found in a full audit of
  every "Current"-status doc against the actual codebase — a false "no
  migration ledger exists" claim, renamed env vars
  (`WECHAT_TOKEN`/`WECHAT_ENCODING_AES_KEY` → `WECHAT_BOT_TOKEN`/
  `WECHAT_BOT_ENCODING_AES_KEY`) still shown under their old names, a
  missing required env var (`CUSTOMER_CREDENTIAL_KEY`), and the entire
  `/admin/kefu-staff` API surface having no operational documentation.

### Added
- Admin panel, Users & Groups tab: `external_userid` now shows only the
  last 8 characters plus a Copy button (full value in a tooltip and copied
  to clipboard on click), instead of the full ~35-character opaque id.

## [1.2.0] - 2026-09-15

### Added
- `warehouse_admin` role: makes uchoice inbound/outbound requests and
  confirms their own completion, scoped to assigned warehouse(s) — the
  same warehouse-assignment requirement as `warehouseman`, a different
  service grant set (`uchoice_inbound_request`, `uchoice_outbound_request`,
  `confirm_inbound_completion`, `confirm_outbound_completion`). New
  migration `V32`.
- `core/role_policy.py`: one shared, typed declaration of role-level
  assignment policies (warehouse scope, billing-customer identity),
  replacing four separately-duplicated `role.name == "warehouseman"` /
  `in CUSTOMER_IDENTITY_ROLE_NAMES` branches across
  `api/admin/members.py`, `api/admin/kefu_staff.py`,
  `handlers/uchoice/role_change.py`, and
  `core/pre_confirm_validators.py`. See
  [ADR-010](docs/architecture/decisions/adr-010-role-service-policy-declarations.md).
  - `GET /admin/roles` now returns a generic `required_fields` descriptor
    list per role (label, value type, choice source) instead of two ad hoc
    `customer_identity`/`warehouse_scoped` booleans.
  - `POST /admin/roles/{role_id}/services/{service_type_id}` now 409s if
    granting would give an already-existing, incompletely-provisioned
    warehouse-scoped assignment reachability to a warehouse-scoped
    service.
  - New `scripts/check_role_policy_impact.py`: manual, read-only pre-deploy
    gate for a role-classification change in `core/role_registry.py`
    itself (declare the role, run the check, then deploy) — a code-level
    change has no admin-API call site to hook an automatic check onto.
- `CUSTOMER_IDENTITY_ROLE_NAMES` (`fedex_label_agent` alongside
  `customer`): generalizes the previously-hardcoded `"customer"` check to
  any role representing an external customer's own billing identity.
  `kefu_staff` gains `billing_customer_id` (`V31`), mirroring
  `group_member`'s existing column. See
  [ADR-009](docs/architecture/decisions/adr-009-customer-identity-roles.md).
  `fedex_label_agent` made assignable (FedEx-only labels for one
  customer's own account).
- `DELETE /admin/roles/{role_id}`: role deletion, rejecting protected roles
  (`admin`, `pending`) and any role still assigned to a member/staff row.
- Admin panel: `billing_customer_id` input on the Kefu Staff tab, shown
  only for customer-identity roles; Delete button on the Roles tab.

### Fixed
- **Warehouse-scope enforcement was fail-open, not fail-closed, for a
  misprovisioned scoped caller.** A `warehouseman`/`warehouse_admin` with
  no `warehouse_codes` actually assigned was treated as unrestricted at
  multiple runtime checkpoints (`core/pre_confirm_validators.py`,
  `handlers/uchoice/storage_txns.py`, `handlers/uchoice/record_request.py`,
  `handlers/uchoice/lookup_validate.py`, `handlers/uchoice/address.py`)
  instead of being rejected. All now consult one shared
  `core.role_policy.check_warehouse_scope`.
- Rejecting a warehouse-scope violation during `confirm_inbound_completion`/
  `confirm_outbound_completion` could mark the *original*, unrelated,
  valid target request failed instead of only cancelling the rejected
  confirmation attempt — fixed by raising `TargetValidationError` instead
  of a bare exception in the completion-lookup and completion-storage
  handlers.
- YiDiDa `/price` quote requests never sent the shipper's origin address,
  silently pricing every quote against some default/account-level location
  instead of the real shipper (confirmed live: a $38.75 → $55.95
  difference for the same shipment once fixed).
- `keHuDanHao` (label reference number) could contain raw Chinese
  characters from a Kefu staff member's real WeCom nickname; now stripped
  before truncation.

## [1.1.0] - 2026-09-11

### Added
- New `customer`/`customer_credential`/`label_shipment` tables and
  `core/customer_directory.py`: a central, `F######`-keyed customer
  master-data module embedded in this service (not yet a separate
  deployment), holding profile data, per-carrier `ydd_channel_id`/
  `rate_multiplier` (reserved for a future pickup-scheduling pipeline,
  unused by labels), and AES-256-GCM-encrypted OMS/YDD credentials
  (`customer_credential`, write-only at the API layer — no endpoint ever
  returns a decrypted or encrypted value).
- `GroupMember.billing_customer_id`: a new, explicitly-named (not
  `customer_id`, to avoid colliding with the pre-existing
  `uchoice_customer`-linked concept) binding from a customer-role member to
  their `customer` row. Enforced end-to-end: admin API
  (`api/admin/members.py`), conversational role changes
  (`handlers/uchoice/role_change.py`), and label-creation turns
  (`core/customer_directory.resolve_billing_customer_id`, wired into both
  `core/workflow_engine.py` and `core/kefu_turn_apply.py`) all require,
  validate, and clear this binding consistently — a customer-role caller's
  own binding is always authoritative and can never be overridden by a
  supplied value; an unbound customer-role caller is rejected outright
  rather than falling through to staff-style validation.
- Admin panel: new "Customers" tab (list/create, `status`, `oms_wh_code`,
  `ydd_channel_id`/`rate_multiplier` JSON editors, write-only "Set/Rotate"
  credential actions).
- `clients/yidida_client.py::get_price_quote()`: calls YiDiDa's separate
  `/price` endpoint (label creation's `/yundans` carries no pricing fields)
  to fetch `sales_amount` for a completed label. Wired into
  `handlers/label/base.py` right after label creation, non-fatally (a
  quote failure never blocks label delivery); the result is persisted by
  `handlers/oms_create_workorder.py` into the new `label_shipment` table.
- `label_agent` added as an assignable role name; `fedex_label`/
  `ups_label` are now grantable and enabled on the Kefu channel as well
  (previously Smart-Bot-only despite being grantable via
  `group_service_role`).
- OMS work orders now attach a `物流费` (logistics fee) VAS line item
  (`workVasitemList`) with `qty = int(estimated quote price)`, using
  hardcoded catalog values (`VAS_LOGISTICS_FEE_BILL_ITEM_ID`/`_RULE_ID` in
  `clients/oms_client.py`) rather than a live per-request lookup — the VAS
  catalog doesn't change per shipment, so `scripts/fetch_oms_vas_list.py`
  is a standalone, not-wired-into-the-pipeline CLI for refreshing these
  values by hand if the catalog is ever updated. Estimated price only; the
  qty is expected to be corrected manually once a carrier invoice comes in.
- New `company_warehouse` table (`V29`) and `core/warehouse_directory.py`:
  the company's own physical shipping-origin directory (`JFK`/`DE`/`LAX`/
  `ORD`/`NJ`, each with a `company_name` billing entity — most are
  `TWF-*`, `NJ` is `TWW`), deliberately separate from U-Choice's
  `VALID_WAREHOUSE_CODES` concept. Injected into AI context so a bare
  warehouse abbreviation in a label request (e.g. "从LAX到DE") resolves to
  a full address on whichever side — shipper or recipient — the phrasing
  indicates; both directions are handled explicitly since a request like
  "送一个包裹到JFK" makes our warehouse the *recipient*, not the shipper.
  Admin panel "Warehouses" tab and `/admin/warehouses` CRUD.
- Global role→service permission model: new `role_service_permission`
  table (`V30`, role_id + service_type_id, no group_id) replaces the old
  per-group `group_service_role`. A role's actually-reachable services are
  now the intersection of this global grant and the group's own
  `group_service` (still per-group — real tenant differentiation).
  Confirmed lossless: every existing grant already targeted the same
  single production group. `core/role_registry.py` relocates and enriches
  `ASSIGNABLE_ROLE_NAMES` (now backed by an `AssignableRole` dataclass with
  a description) out of `core/uchoice_constants.py`. New global endpoints
  `GET/POST/DELETE /admin/roles/{role_id}/services[/{service_type_id}]`
  replace the removed per-group `/admin/groups/{group_id}/services/
  {service_type_id}/roles`. Admin panel gets a "Roles" tab: role catalog
  with an assignable/not badge, a "+ New role" form, and a per-role
  permission checklist against the full service catalog.

### Fixed
- `clients/yidida_client.py`: label creation (`/yundans`) never actually
  applied the package dimensions it sent — `changDu`/`kuanDu`/`gaoGao`
  don't exist in YiDiDa's real request schema and were silently dropped;
  the real fields are `danJianList[].chang/kuan/gao`, in cm, and weight
  needed lbs→kg conversion (`shouHuoShiZhong` was previously sent
  unconverted, ~2.2x off). Fixed by rebuilding the body against YiDiDa's
  real Swagger schema. The separate quote endpoint (`/price`) needed an
  entirely different `unitModelList` (English field names, also cm/kg)
  shape, previously missing outright.
- `clients/oms_client.py::_sign()` only sorted top-level dict keys before
  computing the HMAC-SHA256 signature; every payload had been flat until
  `workVasitemList` (nested dicts) was introduced above, which OMS then
  rejected with `[11006] 验签不通过`. Fixed with a recursive
  `_deep_sort_keys()`.
- Label success messages on Kefu no longer show a redundant
  "[点击下载标签]" line — the label file is already attached as a native
  message on that channel; Smart Bot (which has no file-attachment path)
  keeps the download link.
- Label confirmation messages now warn when the customer never provided
  package dimensions, instead of silently proceeding with defaults; and
  render missing/optional fields as "系统默认" instead of Python's bare
  `None`.

### Changed
- `handlers/label/base.py` now reads OMS/YDD credentials from
  `customer_directory` instead of `group_service.config`, and revalidates
  the `billing_customer_id` binding immediately before calling YDD (not
  just at field-collection time), catching a customer deactivated or a
  member rebound between collection and confirmation.
- `handlers/oms_create_workorder.py` is now customer-driven and
  failure-tolerant: missing credentials or a missing `oms_wh_code` skip
  the OMS call cleanly (recorded, not fatal), and OMS API failures
  (timeouts included) are caught and recorded rather than aborting the
  turn and rolling back an already-created label.

## [1.0.4] - 2026-09-04

### Added
- Outbound order-creation confirmation now shows the internal-transfer
  warning ("⚠️ 此为内部调仓...") whenever the destination resolves to one of
  the company's own warehouses, not just at warehouse-completion time.
  Worded in future tense (`core/confirmation.py`'s `_outbound_sections_builder`)
  since confirming at creation only advances the request to `processing` —
  no inventory moves until a warehouseman later confirms completion, where
  the existing present-tense warning still applies unchanged.
- Admin panel: Transactions tab gets a page-size selector (25/50/100,
  default 25), wired to the existing `page_size` query param on
  `GET /admin/request-logs`.

### Fixed
- `core/session_manager.py`'s `_build_uchoice_candidates()` gated the
  pending/cancelable candidate list for `confirm_inbound_completion`,
  `confirm_outbound_completion`, `cancel_inbound_request`, and
  `cancel_outbound_request` on whether the CALLER's own role also held the
  paired creation service (e.g. `uchoice_outbound_request`). A pure
  warehouseman — whose entire job is completing requests, never creating
  them — never holds that grant, so the candidate list was silently empty
  regardless of real `request_log` content. Found live in production: a
  warehouseman ("Jeff") was rejected with "当前没有待处理的出库申请，无需
  操作。" on a genuine `processing` outbound request assigned to his
  warehouse, on two separate days, across both the pending-completion and
  (latently, since no role's real grants ever triggered it) the
  cancellation paths. The paired service's `service_type_id` is now
  resolved via a global `service_type` catalog lookup, independent of both
  the caller's own grants and the group's current service enablement
  (`request_log.service_type_id` has no dependency on `group_service` at
  all, so an admin disabling new-order intake for a group must not hide
  already-processing requests from completion/cancellation either).
- A first outbound-request message naming both a source and destination
  warehouse in one breath (e.g. "从NJ仓到DE仓") had its address-candidate
  list built from the still-empty `collected_fields`, before the AI call
  that would extract `warehouse_code` from that same message — silently
  defaulting to JFK-only addresses and hiding the real NJ-scoped
  destination entirely (each warehouse has its own same-named address row
  for its own inter-warehouse transfers). The AI could only match the
  JFK-scoped one, which `core/pre_confirm_validators.py` then correctly
  rejected at confirmation time — a needless failure, not a safety gap.
  `core/session_manager.py` now scans the raw message text for bare
  JFK/DE/NJ mentions as a same-turn scoping hint; `ai/prompt_builder.py`
  also now tells the AI a bare warehouse-code mention refers to our own
  warehouse, not an unrecognized new address.

## [1.0.3] - 2026-09-04

### Added
- Admin panel: a **Transactions** tab — a paginated, filterable ledger over
  `request_log` (status/channel/date-range filters, keyset pagination),
  with each row expandable to show every `conversation_session` that ever
  touched that request (not just the first), rendered as labeled,
  HTML-escaped conversation transcripts.
- Admin panel: **Staff & Groups** tab consolidates the existing Kefu Staff
  and Groups/Members sections into one tabbed view.
- Kefu Staff warehouse assignment is now checkboxes sourced from the
  server's own `VALID_WAREHOUSE_CODES` (via a new `warehouse_codes` field
  on `GET /admin/roles`), replacing a free-text input that only found out
  about a typo after submitting.
- `GET /admin/request-logs` (existing endpoint) gains `source_channel` in
  its response, a `kefu_staff` join for Kefu rows' `display_name`
  (previously always `None` for Kefu), and a `sessions` array on the
  detail endpoint for the ledger's conversation view.
- `db/migrations/V25__conversation_session_request_log_index.sql` — indexes
  `conversation_session(request_log_id)`, the ledger's per-row conversation
  lookup.

### Fixed
- A `targets_existing_request` service (`confirm_inbound_completion`,
  `confirm_outbound_completion`, `cancel_inbound_request`,
  `cancel_outbound_request`) unconditionally created a placeholder
  `conversation_session`/`request_log` pair before knowing whether a real
  target existed. Four separate rejection paths — zero eligible
  candidates, an explicitly-referenced serial that doesn't exist, one that
  exists but isn't `processing`, and a single eligible candidate missing
  its own serial number — left that placeholder behind permanently
  (`status='pending'`, real user text, never resolved). Found live in
  production (`REQ-20260903-000022`, unresolved since creation). All four
  paths now discard the placeholder and end the session terminal in one
  step (`core/kefu_turn_apply.py`).
- `GET /admin/request-logs` raised a Pydantic validation error on any
  Kefu-originated row — `wechat_openid` was typed as required `str`, but
  Kefu rows genuinely store it as `NULL` (Kefu identifies by
  `submitted_by_staff_id` instead). This endpoint had likely never
  successfully listed a single Kefu request before this fix.
- Its date filters used `datetime.fromisoformat(...).replace(tzinfo=utc)`,
  which relabels an offset-aware timestamp instead of converting it (a
  `-04:00` input was silently treated as `+00:00`, off by however many
  hours the offset was). Now converts via `.astimezone(timezone.utc)`.
- A cursor that was valid base64/JSON/ISO-datetime but carried a
  non-UUID `log_id` passed `_decode_cursor`'s own checks and reached the
  PostgreSQL keyset query uncaught, surfacing as a raw 500 instead of a
  400. `log_id` is now validated as a real UUID in the same decode step.
- The admin panel's ledger date-to filter sent `T23:59:59`, excluding
  anything in the final fractional second of the selected day; now
  `T23:59:59.999999`, the true end of the day at Postgres's own
  microsecond precision.
- Multiple sessions sharing an identical `created_at` (possible since
  `now()` returns transaction-start time, not per-statement time) had no
  deterministic order; `session_id` is now a tie-breaker.

## [1.0.2] - 2026-09-03

### Fixed
- `cancel_inbound_request`/`cancel_outbound_request` were completely
  unreachable: the AI intent-classification prompt's existing `cancel`
  rule (written before these services existed) gave the generic
  mid-session "abandon whatever's in front of you" intent unconditional
  priority over any message containing "取消", with an explicit
  instruction not to let candidate/service matching override it. Every
  attempt to actually cancel a processing request — even one explicitly
  naming its serial number — was silently swallowed as a no-op abandon of
  whichever session happened to be active, never touching the target
  request at all. `ai/prompt_builder.py` now carves out an explicit
  exception: a message naming a specific already-submitted request (by
  serial number, or by the cancel services' own keyword phrases) routes to
  the real cancellation service regardless of what else is active in the
  current session.

## [1.0.1] - 2026-09-03

### Added
- Warehousemen and Kefu staff can now be assigned more than one warehouse
  (`group_member.warehouse_codes` / `kefu_staff.warehouse_codes`, a Postgres
  array replacing the old single `warehouse_code` column). Enforced
  consistently across both channels: Kefu case authorization, completion
  lookup, and — closing a gap that existed even under the old single-value
  design — `adjust_storage`, `move_storage`, `recount_storage`, inbound/
  outbound request creation, `view_storage`, `view_storage_history`,
  `view_invoice`, and `upsert_address`.
- New `cancel_inbound_request` / `cancel_outbound_request` services let a
  request's original creator (or an admin, within their own group) cancel
  an inbound/outbound request after it's been confirmed but before a
  warehouseman completes it — previously the only way out of that state was
  completion, or (Smart Bot only) an automatic 7-day staleness sweep.
- Added **NJ** (120 Raskulinecz Rd, Carteret, NJ 07008) as a third
  warehouse, with a full internal-transfer address matrix to/from JFK and
  DE and a self-pickup entry.
- `db/migrations/V22__warehouse_assignments_as_array.sql`,
  `V23__cancel_pending_uchoice_requests.sql`,
  `V24__add_nj_warehouse.sql`.

### Fixed
- Both channels' turn finalizers (`core/workflow_engine.py`,
  `core/kefu_turn_apply.py`) unconditionally overwrote a target request's
  status right after workflow steps ran; this would have silently reverted
  every cancellation back to `success`. Finalizers now check the target's
  current status before advancing it.
- Neither pipeline previously locked a target request before checking its
  status, so a completion attempt and a concurrent cancellation attempt
  could both observe `processing` and both proceed. Both now use a locked,
  identity-map-refreshed read (`with_for_update()` + `populate_existing()`)
  as the first and only authoritative status check.
- A losing attempt in that race no longer marks the target request
  `failed` — every rejection of a targets_existing_request operation
  (concurrency loss, wrong direction, wrong group, not authorized) is a
  typed business conflict (`TargetOperationRejected` and its subclasses,
  `core/workflow_errors.py`) that closes only the attempting turn; the
  target is left exactly as it already was. This also closed a
  pre-existing gap in the original completion handler, which had the same
  bare-exception pattern.
- `cancel_inbound_request`/`cancel_outbound_request` did not actually
  check that a referenced request matched the expected direction, despite
  the validator's own docstring claiming it did — an outbound serial could
  reach an inbound-cancellation confirmation.
- A warehouseman restricted to one warehouse could reassign an existing
  address's warehouse to their own via `upsert_address`'s
  `matched_address_id`, even when that address currently belonged to a
  warehouse they have no authority over. The warehouse-scope check for
  address updates now also validates the address's *current* warehouse,
  not just the requested one.
- `V23`'s hardcoded `service_type`/`workflow` UUIDs collided with rows
  already claimed by `V10` (missed because only `V2` and `V15` were
  checked when picking new ones) — caught when applying to production;
  moved to genuinely free UUIDs before this version's migrations were
  first successfully applied anywhere.

### Documentation
- Archived the reviewed design plan for the above under
  `docs/archive/collaboration/2026-09-warehouse-array-and-cancel-service/`.

[Unreleased]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.4...HEAD
[1.0.4]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.0...v1.0.1
