# U-Choice Pipeline — Design Reference
# Logistics WeChat Bot Platform — V1.1/V2 expansion

**Status:** Implemented (V3–V8) and live-tested as of 2026-08-05. This document
is the **original design record** — it captures the reasoning behind the
decisions below, and most of it still holds, but a number of specifics were
found wrong or incomplete only once actually built and tested against a real
DB and a real AI model (multi-month date ranges, several confirmation/response
formatting bugs, a request-lifecycle gap, a cross-direction validation gap —
see `docs/data-model.md`'s per-column notes and the migration list there for
exactly what changed and why). For current schema, read `docs/data-model.md`.
For the process to follow when adding another service, read
`docs/ops/adding-a-service.md` — it was written directly from what broke and
had to be fixed while building this pipeline. If this document conflicts with
either of those on any concrete detail, they win.

**Business model, established through this design pass:** U-Choice is a company
that owns and distributes its own packing-supply product line (stretch wrap, tape).
It is **not** a multi-tenant 3PL storing separate customers' own goods — the
inventory in `uchoice_storage` belongs to U-Choice alone, shared across whichever
group(s) interact with the bot. This single fact is why several tables below have
**no `group_id`** — that was a real design correction made partway through this
session (`uchoice_storage`, `uchoice_storage_txn`, `uchoice_address`,
`uchoice_storage_fee_ledger` all dropped `group_id` after initially including it
by habit, copied from the multi-tenant FedEx/UPS side of the platform where it
*does* apply).

**MVP group structure:** one shared WeChat group for U-Choice, all four roles as
members (`customer`, `warehouseman`, `accountant`, `admin`), gated entirely by
`group_service_role` (deny-by-default), not by physical group separation. A
dedicated ops/broadcast-group design was considered and explicitly deferred — see
Backlog at the end.

---

## Roles

Four roles, added to the existing `role` table (`admin`, `customer` already exist
from the base platform):

| Role | Purpose |
|---|---|
| `admin` | Full access to every service (blanket grant, same pattern as the base platform) |
| `customer` | Places inbound/outbound requests, views storage/invoice, manages addresses |
| `warehouseman` | Confirms completions, corrects storage (adjust/recount/move) |
| `accountant` | Views storage and invoice — read-only financial visibility |

`group_member.warehouse_code` (new nullable column) — required only for
`warehouseman` (enforced at the API layer in `api/admin/members.py`, same pattern
as `group_config_schema.required` validation elsewhere — not a DB CHECK, since a
CHECK can't reach across the FK to know the role's name). Cleared to `NULL`
automatically whenever a member's role changes away from `warehouseman`.

---

## New/changed platform-level schema (affects the base platform, not just U-Choice)

These aren't U-Choice-specific tables — they're changes to existing shared
infrastructure that U-Choice's design surfaced a need for.

### `service_type` — two new columns
```sql
ALTER TABLE service_type ADD COLUMN requires_confirmation BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE service_type ADD COLUMN targets_existing_request BOOLEAN NOT NULL DEFAULT FALSE;
```
- `requires_confirmation = false` — skips the confirm/cancel ceremony entirely;
  workflow executes immediately once `all_fields_collected` fires. Used by the
  read-only services (`view_storage`, `view_storage_history`, `view_invoice`).
  **Needs a real `workflow_engine.py` change** — today `_trigger_confirmation()`
  always builds a template and waits; this flag needs a branch that runs the
  workflow steps immediately instead.
- `targets_existing_request = true` — the service locates and **updates** an
  existing `request_log` row (by `reference_serial`) instead of creating a new
  one. Used by `confirm_inbound_completion`/`confirm_outbound_completion`. Also
  needs a `workflow_engine.py` change — `_handle_new_request()` needs to skip
  its normal "create a fresh request_log" step for these.

### `group_member` — one new column
```sql
ALTER TABLE group_member ADD COLUMN warehouse_code VARCHAR(20);
```
Nullable; meaningful only for `warehouseman`. Required-for-that-role enforced at
the API layer, not the DB.

### `group_config` — one new column
```sql
ALTER TABLE group_config ADD COLUMN group_robot_webhook_url TEXT;
```
WeChat Work's **群机器人 (Group Robot) Webhook** — a persistent, static per-group
URL that can be POSTed to at any time, unlike `response_url` (single-use, tied to
one inbound webhook event, ~1hr validity). Needed because the daily broadcast job
and the monthly invoice push are both scheduled/proactive, not replies to a live
message — `response_url` structurally cannot be reused for either.

**Confirmed against the official doc** (developer.work.weixin.qq.com/document/path/91770):
set up by an admin right-clicking an existing group → 添加群机器人 → returns
`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXX`. No token, no
"app must have created the chat" restriction (unlike `appchat/send`, which was
briefly considered and ruled out for exactly that reason — see
`memory/wecom_api_reference.md`). Payload shape
(`{"msgtype":"markdown","markdown":{"content":...}}`) matches what
`clients/wechat_client.send_message()` already sends — reusable with just a
different target URL, no new send logic needed. @mention confirmed supported via
`<@userid>` inline syntax on `text`/plain `markdown` (not `markdown_v2`), or the
structured `mentioned_list` field on `text` type. Rate limit: 20 msg/min per robot.

### `request_log.status` — expanded CHECK constraint
Old values: `processing, success, failed, timed_out`. New full set:

| Status | Meaning |
|---|---|
| `pending` | Renamed from the old `processing` — awaiting customer confirm/cancel |
| `processing` | Re-used for a new meaning — confirmed, awaiting completion. Momentary for single-shot services (FedEx), long-lived (hours/days) for U-Choice's two-step inbound/outbound flow |
| `success` | Completed |
| `failed` | Execution error |
| `cancelled` | Customer explicitly cancelled before confirming (new — `_handle_cancel()` today doesn't touch `request_log` at all, a pre-existing bug this fixes) |
| `timed_out` | Session expired before confirmation — pre-confirm abandonment |
| `stale` | New — a `processing` request that sat too long (7 days) without warehouse completion. Terminal, like `cancelled`. The parallel to `timed_out`: same idea, different lifecycle stage (pre- vs post-confirm abandonment) |

**`request_log` creation timing also changes**: created at `new_request` time
(once a `service_type` resolves), not inside `_trigger_confirmation()` as today.
This was driven by "log every resolved request, confirmed or not" — covers
`cancelled` and `stale` properly, and is a prerequisite for `requires_confirmation
= false` services to have any log at all.

### New table: `interaction_log` (platform-wide, not U-Choice-specific)
Separate from `request_log` — write-once, append-only, one row per incoming
message once intent is classified, **regardless of outcome** (including small
talk and rejected/unrecognized messages). Purpose: efficiency/funnel analysis
("what fraction of a group's traffic becomes real work"), decoupled from
`request_log`'s stricter lookup/update needs.

```sql
CREATE TABLE interaction_log (
    interaction_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wechat_openid   VARCHAR(128) NOT NULL,
    group_id        UUID REFERENCES group_config(group_id) ON DELETE SET NULL,
    intent          VARCHAR(30) NOT NULL,
    intent_type     VARCHAR(20) NOT NULL,
    service_type_id UUID REFERENCES service_type(service_type_id) ON DELETE SET NULL,
    request_log_id  UUID REFERENCES request_log(log_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`intent` = the AI's raw classification (`new_request`, `continuation`, `confirm`,
`cancel`, `check_services`, `unrecognized`), plus `rejected` (set by the workflow
engine, not the AI, when `_find_service()` fails to match — access denied or no
such service). `intent_type` is a derived category, computed once at insert time
from a fixed mapping, for cheap `GROUP BY` analysis without a `CASE` expression
every query:

| intent | intent_type |
|---|---|
| new_request, continuation, confirm | productive |
| cancel | abandoned |
| check_services | informational |
| unrecognized | noise |
| rejected | denied |

---

## Rate constants (hardcoded in Python, not DB tables — per explicit choice)

| Charge | Amount | Applies to |
|---|---|---|
| `short_delivery` | $30 | Outbound, `uchoice_address.charge_type` |
| `delivery` | $45 | Outbound, `uchoice_address.charge_type` |
| `truck_transfer` | $85 | Outbound, `uchoice_address.charge_type` — also used for **inter-warehouse transfers** (JFK↔DE), where the "destination" is U-Choice's other warehouse |
| Palletization | $15/pallet | Outbound, `new_pallet_count` field — customer wants loose boxes consolidated onto a fresh pallet before shipping |
| Unpacking | $300 flat | Inbound, `needs_unpacking` boolean — symmetric to palletization, warehouse needs to break down a non-palletized inbound shipment |
| Storage | $1/pallet/day | Computed daily via `uchoice_storage_fee_ledger`, warehouse-wide (see below) |

`charge_type` was renamed from an initial `distance_tier` naming — the tiers
aren't really about geographic distance, they're about delivery **method**
(`truck_transfer` specifically implies inter-warehouse trucking, not "far away").

---

## U-Choice-specific tables

### `uchoice_sku` — catalog
8 real SKUs (from the actual U-Choice product export), excluding `uchoice_plt`
(通用托盘 — a unit-of-measure helper for storage math, not a trackable/shippable
product itself):

| sku_code | Description |
|---|---|
| s1 | S1 22 lb Stretch Wrap |
| s2 | S2 1500 ft Stretch Wrap |
| s3 | S3 Black Stretch Wrap |
| s4 | S4 1000 ft Stretch Wrap |
| t1 | T1 3-inch Clear Packing Tape |
| t2 | T2 3-inch Dark Brown Packing Tape |
| t3 | T3 3-inch Light Brown Packing Tape |
| t4 | T4 2-inch Clear Packing Tape |

### `uchoice_storage` — current balance, **no `group_id`**
```sql
CREATE TABLE uchoice_storage (
    warehouse_code   VARCHAR(20) NOT NULL,
    sku_code         VARCHAR(50) NOT NULL REFERENCES uchoice_sku(sku_code),
    boxes_per_pallet INTEGER NOT NULL,
    pallet_count     INTEGER NOT NULL DEFAULT 0 CHECK (pallet_count >= 0),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (warehouse_code, sku_code, boxes_per_pallet)
);
```
**Key design point:** `boxes_per_pallet` is a free integer, not a pre-registered
catalog value (no separate `uchoice_pallet_config` table). Buckets are created
dynamically the first time a given box-count occurs — because real box counts
drift from ad-hoc partial picks (an 80-box pallet becomes a 77-box pallet after a
3-box pick), so there's no fixed enumeration of valid configs. The
`CHECK (pallet_count >= 0)` is the negative-balance safety net — an attempted
decrement below zero fails at the DB level; handlers catch it and give a clean
"库存不足" message instead of leaking the raw constraint error.

Two warehouses only: `JFK`, `DE`.

### `uchoice_storage_txn` — audit log, **no `group_id`**
```sql
CREATE TABLE uchoice_storage_txn (
    txn_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_code   VARCHAR(20) NOT NULL,
    sku_code         VARCHAR(50) NOT NULL,
    boxes_per_pallet INTEGER NOT NULL,
    pallet_delta     INTEGER NOT NULL,
    txn_type         VARCHAR(20) NOT NULL CHECK (txn_type IN
                        ('inbound','outbound','convert_in','convert_out',
                         'move_in','move_out','adjust','recount')),
    request_log_id   UUID REFERENCES request_log(log_id) ON DELETE SET NULL,
    note             TEXT,
    created_by       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
`convert_in`/`convert_out` = customer-fulfillment loose-box picks (during
`confirm_outbound_completion`). `move_in`/`move_out` = internal warehouse
repacking (`move_storage`) — same underlying math as convert, deliberately
distinct `txn_type` so the audit trail (`view_storage_history`) can tell "part of
fulfilling a customer order" apart from "internal reshuffling," even though the
bucket arithmetic is identical.

### `uchoice_address` — **no `group_id`**, shared/company-wide reference data
```sql
CREATE TABLE uchoice_address (
    address_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name   VARCHAR(200) NOT NULL,
    charge_type    VARCHAR(20) NOT NULL CHECK (charge_type IN
                      ('short_delivery', 'delivery', 'truck_transfer')),
    addr           TEXT NOT NULL,
    warehouse_code VARCHAR(20),   -- nullable; set only when tied to a specific origin warehouse
    note           TEXT,
    created_by     VARCHAR(128) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
No phone/email — deliberately loose, matches the casual nature of the business.
No `alias` field either — considered, then dropped in favor of AI fuzzy-matching
against `company_name`/`addr` (see Cross-Cutting Mechanisms below).

**Seed rows (inter-warehouse transfer addresses)** — these belong in the V3
migration directly, since they're not tied to any group:
```sql
INSERT INTO uchoice_address (company_name, charge_type, addr, warehouse_code, note, created_by) VALUES
('U-Choice DE Warehouse',  'truck_transfer', '201 Gabor DR, Newark, DE 19711',    'JFK', 'DE warehouse', 'system'),
('U-Choice JFK Warehouse', 'truck_transfer', '14502 156th St, Jamaica, NY 11434', 'DE',  'JFK warehouse', 'system');
```
(`company_name` values here are a best guess, not explicitly specified — confirm
before finalizing.)

### `uchoice_storage_fee_ledger` — **no `group_id`**, warehouse-level
```sql
CREATE TABLE uchoice_storage_fee_ledger (
    ledger_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_code VARCHAR(20) NOT NULL,
    fee_date       DATE NOT NULL,
    pallet_count   INTEGER NOT NULL,
    storage_fee    NUMERIC(10,2) NOT NULL,
    UNIQUE (warehouse_code, fee_date)
);
```
Populated daily by the same job that does the broadcast/stale-retirement pass:
sum `uchoice_storage.pallet_count` across all SKUs for a warehouse, × $1, one row
per warehouse per day. (A per-group allocation-tracking design was considered and
explicitly rejected — see the note in Corrections Made During This Session below.)

---

## Services (`service_type` rows)

For each: role, confirmation behavior, key fields, workflow summary.

### `uchoice_inbound_request` — role `customer`
- `requires_confirmation = true`, `targets_existing_request = false`
- Required: `warehouse_code`, `sku_lines` (array — pallet-type
  `{sku_code, boxes_per_pallet, pallet_count}` or loose-type `{sku_code, box_count}`)
- Optional: `needs_unpacking` (boolean, $300 flat if true — **always shown** in
  confirmation regardless of value, same reminder principle as `new_pallet_count`
  below, not silently omitted when false)
- Workflow: `record_request → reply_wechat`. **No storage change at this point**
  (two-step design — storage only changes at `confirm_inbound_completion`). No
  immediate warehouse notification either (daily broadcast handles that, not a
  per-request push)

### `uchoice_outbound_request` — role `customer`
- `requires_confirmation = true`
- Required: `warehouse_code`, `sku_lines` (same two line shapes as inbound),
  `destination_address_id` (resolved via AI fuzzy-matching against injected
  `uchoice_address` context — see Cross-Cutting Mechanisms)
- Optional: `new_pallet_count` ($15/pallet — **always shown** in confirmation)
- **No-guess rule for pallet-type lines missing `boxes_per_pallet`:** the current
  `uchoice_storage` buckets for that SKU+warehouse are injected as context; the
  AI proposes the **largest available bucket** as a default, shown explicitly in
  the confirmation so the customer can correct it (handled as a `continuation`
  intent, not a new "update" intent type — see below). Any correction is
  validated against the actual available buckets, not accepted as an arbitrary
  number.
- Workflow: `record_request → reply_wechat`, same no-storage-change-yet pattern

### `confirm_inbound_completion` — role `warehouseman`
- `requires_confirmation = true`, **`targets_existing_request = true`**
- Optional: `reference_serial` — if omitted, fuzzy-matched against injected
  candidate list (pending `processing`-status inbound requests, scoped to the
  confirming warehouseman's own `warehouse_code`). 0/1/N candidate handling: 0 →
  "nothing pending," 1 → proceed to confirm, N → list and ask to disambiguate
- Optional: `received_lines` — defaults to the original request's `sku_lines` if
  unstated (pallet-type lines only); **loose-type lines always require explicit
  restatement**, no sensible default exists for those (system can't guess what a
  warehouseman physically received)
- Validation chain before executing: request exists → `status == processing` →
  confirming warehouseman's `warehouse_code` matches the request's → (implicitly)
  direction matches, since the candidate query is already inbound-scoped
- Discrepancy handling: reported quantities differing from the original request
  are **not blocked** — recorded as ground truth, since the warehouseman is
  reporting physical reality
- Workflow: `lookup_and_validate → apply_storage_txn (inbound) → generate_receiving_confirmation_pdf → update original request_log to success → reply_wechat` + cross-group push to the customer via `group_robot_webhook_url`

### `confirm_outbound_completion` — role `warehouseman`
- Same shape as inbound completion, mirrored for outbound
- `fulfillment_lines`: pallet-type lines can default to "shipped as requested";
  **loose-type lines require explicit `source_boxes_per_pallet` +
  `resulting_boxes_per_pallet`**, never defaulted — this is the loose-box convert
  scenario ("s1-80一托2，从一个80的托上拿的，还剩77" → `convert_out(s1,80,-1)` +
  `convert_in(s1,77,+1)`, bucket created if new)
- Arithmetic sanity check: `source − resulting` should match the requested
  `box_count`; mismatches noted, not blocking
- PDF here is the **delivery** slip (includes the transportation + palletization
  charge), not a plain receiving confirmation

### `view_storage` — role `customer, warehouseman, accountant, admin`
- `requires_confirmation = false` — immediate execution, no confirm/cancel wait
- **Both `warehouse_code` and `sku_code` optional** — omitting either/both
  broadens scope up to "everything, both warehouses, all SKUs"
- Still writes a `request_log` row (per the "log every resolved request"
  boundary), status goes straight through to `success` in the same turn
- No per-role content filtering — once past the two access gates (registered
  member + role granted this service), everyone sees the identical shared-pool
  numbers

### `view_storage_history` — role `customer, warehouseman, accountant, admin`
- `requires_confirmation = false`
- Required: `warehouse_code`, `target_month` (month granularity chosen over a
  free date range — matches `view_invoice`'s granularity and is far more
  reliable for the AI to extract than an ambiguous range)
- Reads `uchoice_storage_txn`, shows all txn types in the window chronologically

### `adjust_storage` — role `warehouseman`
- `requires_confirmation = true`
- **Scope, narrowed after discussion:** standalone delta corrections not covered
  by recount or move — damage, loss, a single spot-check discrepancy, fixing a
  past mistake. Not a full snapshot (that's `recount_storage`), not a
  redistribution (that's `move_storage`)
- Required: `warehouse_code`, `adjustment_lines` (array —
  `{sku_code, boxes_per_pallet, pallet_delta, reason}`, plural to let one recount
  session report several corrections in one message)
- Creates its own new `request_log` row (not `targets_existing_request`)

### `recount_storage` — role `warehouseman`
- `requires_confirmation = true`
- Required: `warehouse_code`, `inventory_lines` (a **full snapshot**, not a
  delta — `{sku_code, boxes_per_pallet, pallet_count}`)
- **Diff-and-adjust mechanism** (not a destructive wipe-and-rebuild): fetch every
  existing bucket for the warehouse, union against the reported snapshot, delta =
  reported − current (0 if a bucket is new, 0 if a bucket is omitted from the
  report — **omission from a full recount means "now zero," not "unchanged"**).
  Confirmation shows the computed diff, not the raw input, so a data-entry
  mistake is catchable. Only non-zero deltas get written (`txn_type = recount`)

### `move_storage` — role `warehouseman`
- `requires_confirmation = true`
- Required: `warehouse_code`, `move_lines` (array —
  `{sku_code, source_boxes_per_pallet, box_count_moved, target_boxes_per_pallet}`)
- Internal repackaging, net-zero total boxes, nothing enters/leaves the warehouse.
  Each line = two convert pairs (`move_out`/`move_in`, 4 txn rows): source bucket
  loses `box_count_moved` boxes (becomes a new/different bucket), target bucket
  gains them (also becomes a new/different bucket)

### `upsert_address` — role `customer, warehouseman`
- `requires_confirmation = true`
- Required: `company_name`, `charge_type`, `addr`
- Create-vs-update resolved the same way as outbound's destination matching: the
  existing `uchoice_address` list is injected as context, the AI matches the
  description against it. If matched → `matched_address_id` extracted, treated
  as UPDATE. If not → treated as CREATE. **Confirmation must explicitly state
  which mode** ("您正在更新..." vs "您正在新增...") so a wrong AI match is
  catchable before it commits — same safety principle as the outbound
  largest-bucket default

### `role_change` — role `admin`
- `requires_confirmation = true`
- Required: `target_openid` (AI-resolved via injected member-list context —
  `wechat_openid` + `display_name` + current role — matching a casual name
  reference like "把张三设为..."), `new_role`
- Conditionally required: `warehouse_code`, if `new_role == warehouseman`
- **Last-admin protection:** before executing, count active `admin`-role members
  in the group; reject if the target being demoted is the only one. Checked
  *before* showing the confirmation template, not after, so the admin doesn't
  waste a round-trip on something that was always going to fail
- Side effect: clears `warehouse_code` to `NULL` if `new_role != warehouseman`
- `target_openid` is always WeChat's real unique ID, never ambiguous even if two
  members share a display name — the only ambiguity is in the AI's *matching*
  step, already covered by the same 0/1/N disambiguation pattern used elsewhere

### `view_invoice` — role `customer, accountant`
- `requires_confirmation = false`
- Required: `warehouse_code`, `target_month`
- **Warehouse-level cost report, not a per-customer bill** — corrected mid-session
  after initially (wrongly) assuming per-group attribution was needed. Since
  `uchoice_storage` has no `group_id` (U-Choice's own inventory, not
  customer-owned goods), there's no "whose pallets" question to answer; this is
  U-Choice's own aggregate operating cost for the month
- `compute_invoice(db, warehouse_code, target_month)`:
  ```
  total = Σ(transportation + palletization, completed outbound requests this month)
        + Σ(unpacking fee, completed inbound requests this month, if flagged)
        + Σ(storage_fee, from uchoice_storage_fee_ledger this month)
  ```
- **Uses `completed_at`, not `created_at`**, to determine "which month" — bill for
  service actually rendered, not just requested (a request submitted July 31 but
  fulfilled August 2 belongs to August's invoice)
- Same `compute_invoice()` function is reused by the monthly scheduled push —
  one computation, two triggers (on-demand + automatic)

### Monthly invoice push — scheduled job, not a chat service
- Reuses `compute_invoice()` exactly
- Pushes into the customer's own group via `group_robot_webhook_url` — this is
  billing information, not a warehouse-ops concern
- Iterates per warehouse×group combination that actually had activity that
  month — no blank invoices sent for zero-activity combinations

### Daily broadcast + stale-retirement job — scheduled, once per day
- Queries all `request_log` rows with `status = processing`, sorted by
  `created_at ASC` (oldest/most overdue first)
- Displays each with a duration annotation ("3天前"); anything past the 7-day
  threshold gets a ⚠️ marker in the same listing
- **Retires anything past 7 days to `status = stale` in the same run** — same job
  that displays the digest also does the cutoff, not a separate process. The
  digest includes a distinct "🗑️ 今日作废" section for anything that just crossed
  the threshold, so nobody's left wondering why a serial number stopped working
- **Also computes that day's `uchoice_storage_fee_ledger` row** in the same pass
  — piggybacks on the fact that this job already runs daily, rather than being a
  separate schedule
- Pushes into the one shared U-Choice group via `group_robot_webhook_url`

---

## Cross-cutting mechanisms (used by multiple services — build once, reuse)

### Candidate-list context injection
Used **four times** across this design: addresses (outbound destination
matching), pending requests (`confirm_completion` fuzzy serial lookup), storage
buckets (outbound no-guess default), member list (`role_change` name resolution).
One shared pattern, not four bespoke ones:

1. `session_manager.build_context()` (runs fresh on every incoming message)
   conditionally fetches a scoped candidate list, based on what services the
   caller's `allowed_services` includes — no point injecting the member list for
   a customer who could never trigger `role_change`
2. Injected into the AI prompt as context, same mechanism as the existing
   `location_presets` block
3. The AI does the matching in the same single-shot call — **this is not real
   AI tool-calling** (the codebase has no tool-use infrastructure anywhere);
   it's pre-fetched context, proven and cheap, not a new architecture layer

### Generic confirmation template
Replaces the current `confirmation.py`'s hardcoded `shipper_*`/`recipient_*`
field-prefix matching. Two pieces:
- A pure generic renderer taking `(serial_number, service_display_name, sections, note)`
  where `sections` is a list of `{label, type: "kv"|"list", items}` — no
  service-specific logic
- A **registry with a default fallback** (`CONFIRMATION_BUILDERS.get(service_type_name, _default_sections_builder)`),
  mirroring `handlers/registry.py`'s idiom. Most services (flat scalar fields,
  nothing to look up) never need a registry entry at all — they use the default.
  Only services needing something the default can't do (array/table rendering,
  a DB lookup like resolving `sku_code → description`) get a bespoke builder.
  This keeps the registry's growth tied to actual formatting complexity, not to
  the total number of services in the system
- **Confirmations must surface ambiguous AI decisions explicitly** — the address
  create-vs-update mode, the proposed largest-bucket default, the recount diff.
  This is the load-bearing safety net for every fuzzy-matching mechanism above:
  the AI can guess, but the human always sees exactly what's about to happen
  before it commits

### Admin contact line
Small helper — looks up `group_member` rows with `role = admin` for the current
group, appends `如有问题请联系{name}` (or a generic fallback if 0 or multiple) to
unrecognized/rejected replies.

### Shared storage-mutation utility
`adjust_storage`, `recount_storage`, `move_storage`, and both `confirm_completion`
services all touch `uchoice_storage`/`uchoice_storage_txn`. The actual mutation
logic (write N txn rows, apply N deltas, let the `CHECK (pallet_count >= 0)`
constraint reject invalid decrements) is written **once** as a shared internal
function; each service's handler differs only in *what deltas it computes* —
straight-through for `adjust_storage`, diffed-against-current for
`recount_storage`, two-per-line for `move_storage`. Three (five) different
AI-facing contracts, one shared mutation implementation.

---

## Corrections made during this design pass (worth knowing the reasoning, not just the conclusion)

- **`uchoice_storage`/`uchoice_storage_txn`/`uchoice_address`/`uchoice_storage_fee_ledger` all had `group_id` removed.** Initially added by habit (every other platform table has it for genuine multi-tenant isolation between unrelated FedEx/UPS customers). Corrected once it became clear U-Choice's inventory is one company's own stock, not multiple customers' segregated goods — there's no "whose pallets" question to answer.
- **A per-group storage-fee allocation-counter table was designed, then explicitly discarded** once the above was clarified — it was solving an attribution problem that doesn't exist here.
- **A dedicated ops/broadcast group (separate from where warehousemen trigger actions) was designed in detail, then walked back** — over-applying a multi-tenant-scaling concern that's real for a *future* expansion but not needed for U-Choice's MVP, where one shared group with role-gating (which the platform already has) is sufficient and simpler. Kept in the backlog below, not deleted.
- **`request_log` creation moved from confirmation-time to new-request-time**, driven by wanting every resolved request logged regardless of outcome (confirmed, cancelled, or stale) — exposed a pre-existing bug where `_handle_cancel()` never touched `request_log` at all.
- **The inbound/outbound `sku_lines` split into two separate service types** rather than one merged service (unlike the earlier FedEx `fedex_label`/`fedex_oms_label` merge) — because the required fields genuinely differ between the two operations, not just one optional field, so the merge lesson from FedEx didn't apply the same way here.

---

## Backlog (explicitly deferred, not MVP)

- **Multi-tenant ops/broadcast group separation** — revisit if a future customer,
  or U-Choice at scale, needs warehousemen servicing many separate groups that
  can't reasonably share one room. Design already sketched above in the
  corrections section: a passive broadcast-only group (zero services,
  `group_robot_webhook_url` for scheduled pushes) separate from wherever the
  actual actions get triggered.
- **Confidence-scored intent classification** — skip explicit confirmation when
  the AI is highly confident, gated by whether the action is read-only (safe) vs.
  a write (risky to skip on). Deferred until there's real usage data showing the
  current friction is actually a problem.
- **Multi-thread / concurrent sessions per user** — the "one active session per
  user per group" constraint stays for MVP. Noted that referencing an old serial
  number already gives partial multi-thread-like behavior without a full
  redesign.
- **PDF generation library/template** — not chosen yet (a candidate like
  `reportlab` was mentioned only as an example, not decided).
- **Audio/Excel/image message support** — explicitly ruled out even for V2,
  "minor improvement after critical functions and UX getting stable."
- **Secondary approval for `adjust_storage`** — raised as a question (no
  customer-side counterpart to cross-check a warehouseman's correction against),
  left as "warehouseman's own confirm is sufficient for MVU, revisit if it
  becomes a real trust/accuracy problem in practice."
- ~~WeChat markdown @-mention verification~~ — **resolved.** Confirmed against
  the official Group Robot Webhook doc: supported via `<@userid>` inline syntax
  or `mentioned_list`, not on `markdown_v2`. See `memory/wecom_api_reference.md`.

---

## Still not written (next steps, not yet done)

- Formal `input_schema` JSON for every U-Choice service (this doc gives field
  lists and shapes; the actual JSON blobs for the migration aren't drafted yet)
- The actual `V3__uchoice_catalog.sql` migration file
- New handler classes (one or more per service, following the existing
  `handlers/registry.py` pattern)
- `workflow_engine.py` changes for `requires_confirmation` and
  `targets_existing_request`
- The daily broadcast job, the monthly invoice job (new files under `jobs/`,
  alongside the existing `jobs/session_expiry.py`)
- Admin API surface for configuring `group_robot_webhook_url`
