# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning started with `v1.0.0` (tagged retroactively at the pre-existing
baseline); prior history predates tagging and isn't broken out by version
here.

## [Unreleased]

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

[Unreleased]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.0...v1.0.1
