# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning started with `v1.0.0` (tagged retroactively at the pre-existing
baseline); prior history predates tagging and isn't broken out by version
here.

## [Unreleased]

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

[Unreleased]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/KenzoRei/Wechat_Bot/compare/v1.0.0...v1.0.1
