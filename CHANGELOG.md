# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning started with `v1.0.0` (tagged retroactively at the pre-existing
baseline); prior history predates tagging and isn't broken out by version
here.

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
