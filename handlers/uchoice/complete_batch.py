import traceback
from datetime import datetime, timezone

from handlers.base import BaseHandler


class _BatchBlocked(Exception):
    """A business outcome that stops the batch with nothing applied: a
    request blocked at its locked re-check, stock that no longer covers a
    request, or picks that differ from what the summary showed."""

    def __init__(self, kind: str, serials: list[str], message: str, target_status: str | None = None):
        self.kind = kind
        self.serials = serials
        self.message = message
        self.target_status = target_status
        super().__init__(message)


class RunCompletionBatchHandler(BaseHandler):
    """
    confirm_inbound_completion_batch / confirm_outbound_completion_batch,
    step 1 (Kefu only). Completes every confirmed request at its original
    quantities, all-or-nothing, reusing the single-request handlers
    (LookupAndValidateCompletionHandler, ApplyInbound/OutboundStorageHandler)
    unchanged per target.

    Lock order and mutation order are deliberately separate (plan review
    finding 1): locking per target as the loop goes lets two batches that
    share no requests but share SKUs deadlock each other. So:
      1. lock every target request row, sorted by log_id (the existing
         locked, refreshed fetch + status/direction/warehouse validation);
      2. acquire the COMPLETE storage-scope union -- origins and transfer
         destinations -- in one globally sorted acquire_storage_scopes call;
      3. re-simulate picks under those locks in display order, compare them
         with the previewed picks, and only then mutate, in display order.

    Everything runs inside one SAVEPOINT. Kefu can't plain-rollback: the
    same transaction holds the CaseExecution claim and replay ledger
    (core/kefu_turn_apply.py's confirm_kefu_turn). Any stop rolls back to the
    savepoint only, so every target is untouched and never marked failed --
    targets aren't owned by this session (same rule as
    handlers/uchoice/lookup_validate.py).

    Never raises for a business outcome: it returns _kefu_stop_workflow
    ("batch_blocked" / "batch_failed") and core/kefu_turn_apply.py's
    _finish_execution re-renders or closes the batch.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core import completion_batch as cb

        direction = config.get("direction")
        fields = context.get("collected_fields") or {}
        serials = list(fields.get("reference_serials") or [])
        indices = context.get("_batch_confirm_indices")
        confirmed = [serials[i - 1] for i in indices if 1 <= i <= len(serials)] if indices else serials
        preview = fields.get("_preview_picks") or {}

        savepoint = db.begin_nested()
        try:
            completed = self._run(db, context, cb, direction, confirmed, preview)
        except _BatchBlocked as blocked:
            savepoint.rollback()
            return {"_kefu_stop_workflow": "batch_blocked", "batch_blocked": {
                "kind": blocked.kind, "serials": blocked.serials, "message": blocked.message,
                "target_status": blocked.target_status, "confirmed": confirmed,
            }}
        except Exception:
            savepoint.rollback()
            print("[complete_batch] unexpected failure, batch rolled back to savepoint:", flush=True)
            traceback.print_exc()
            return {"_kefu_stop_workflow": "batch_failed"}
        savepoint.commit()

        for entry in completed:
            self._defer_customer_group_notice(db, context, entry, direction)
        return {
            "batch_direction": direction,
            "batch_completed": [
                {k: v for k, v in e.items() if k not in ("group_id", "source_channel")} for e in completed
            ],
        }

    def _run(self, db, context, cb, direction, confirmed, preview) -> list[dict]:
        from core.workflow_errors import TargetOperationRejected
        from core.uchoice_storage import acquire_storage_scopes
        from handlers.uchoice.lookup_validate import LookupAndValidateCompletionHandler
        from handlers.uchoice.storage_txns import ApplyInboundStorageHandler, ApplyOutboundStorageHandler

        targets = {}
        for serial in confirmed:
            target = cb.load_target(db, serial)
            if target is None:
                raise _BatchBlocked("rejected", [serial], f"{serial} 未找到该申请")
            targets[serial] = target

        # Phase 1: every request row, in log_id order, before any storage lock.
        uchoice_targets = {}
        for serial in sorted(confirmed, key=lambda s: str(targets[s].log.log_id)):
            sub = self._sub_context(context, targets[serial], {"reference_serial": serial})
            try:
                LookupAndValidateCompletionHandler().handle(sub, {"direction": direction}, db)
            except TargetOperationRejected as e:
                raise _BatchBlocked(
                    "rejected", [serial], e.user_message, getattr(e, "current_status", None),
                ) from e
            uchoice_targets[serial] = sub["_uchoice_target"]

        # Phase 2: the complete scope union, one globally sorted acquisition.
        scopes = cb.storage_scopes([targets[s] for s in confirmed])
        acquire_storage_scopes(db, scopes)

        # Phase 3: re-simulate under lock, compare, then mutate in display order.
        planned = {}
        if direction == "outbound":
            sim = cb.simulate_allocation(cb.load_buckets(db, scopes), [targets[s] for s in confirmed])
            if sim.shortages:
                short = list(sim.shortages)
                raise _BatchBlocked("stock", short, "、".join(short) + " 库存已变动，现有库存不足")
            changed = [s for s in confirmed if not cb.picks_match(preview.get(s), sim.picks.get(s))]
            if changed:
                raise _BatchBlocked("picks_changed", changed, "取货方式已更新")
            planned = sim.picks

        completed = []
        now = datetime.now(timezone.utc)
        for serial in confirmed:
            target = targets[serial]
            if direction == "outbound":
                sub_fields = {
                    "reference_serial": serial,
                    "fulfillment_lines": [
                        {"sku_code": sku, "picks": picks} for sku, picks in sorted(planned[serial].items())
                    ],
                }
                if target.destination_warehouse_code:
                    # Required once lines carry explicit picks -- the handler
                    # no longer derives destination packing itself
                    # (storage_txns.py's _handle_kefu_box_level).
                    sub_fields["destination_packing_lines"] = cb.destination_packing(target)
                sub = self._sub_context(context, target, sub_fields)
                sub["_uchoice_target"] = uchoice_targets[serial]
                try:
                    result = ApplyOutboundStorageHandler().handle(sub, {}, db)
                except TargetOperationRejected as e:
                    raise _BatchBlocked("rejected", [serial], e.user_message) from e
                if result.get("_kefu_stop_workflow"):
                    raise _BatchBlocked("stock", [serial], f"{serial} 库存已变动，现有库存不足")
            else:
                sub = self._sub_context(context, target, {"reference_serial": serial})
                sub["_uchoice_target"] = uchoice_targets[serial]
                try:
                    result = ApplyInboundStorageHandler().handle(sub, {}, db)
                except TargetOperationRejected as e:
                    raise _BatchBlocked("rejected", [serial], e.user_message) from e

            log = target.log
            log.status = "success"
            log.completed_at = now
            # warehouse_code must be present: core/kefu_completion_notice.py
            # filters on result ->> 'warehouse_code'. Same shape the single
            # flow stores (lookup step's warehouse_code + storage result).
            log.result = {"warehouse_code": target.warehouse_code, **(result or {})}
            completed.append({
                "serial_number": serial,
                "warehouse_code": target.warehouse_code,
                "destination_warehouse_code": target.destination_warehouse_code,
                "destination_label": target.destination_label,
                "group_id": str(log.group_id) if log.group_id else None,
                "source_channel": log.source_channel,
            })
        return completed

    @staticmethod
    def _sub_context(context: dict, target, collected_fields: dict) -> dict:
        return {
            "wechat_openid": context.get("wechat_openid"),
            "submitted_by_staff_id": context.get("submitted_by_staff_id"),
            "source_channel": context.get("source_channel"),
            "role": context.get("role"),
            "warehouse_codes": context.get("warehouse_codes"),
            "group_id": context.get("group_id"),
            "request_log_id": str(target.log.log_id),
            "serial_number": target.serial,
            "collected_fields": collected_fields,
            "result": {},
        }

    @staticmethod
    def _defer_customer_group_notice(db, context: dict, entry: dict, direction: str) -> None:
        """
        Same per-request notice CompleteExistingRequestHandler pushes to the
        original request's group, deferred to after Kefu's single outer
        commit (core/kefu_case_adapter.py flushes _deferred_webhook_
        notifications) -- same reason as NotifyCancelledRequestHandler.

        Only for requests created through Smart Robot (a group-chat
        customer). A Kefu-originated request's submitter is notified by
        Kefu's own pull-on-next-message mechanism (core/kefu_completion_
        notice.py, driven by status='success' + completion_notice_shown_at),
        so a group push would be a second, channel-crossing notice.
        """
        if not entry.get("group_id") or entry.get("source_channel") == "kefu":
            return
        from models.group import GroupConfig
        group = db.query(GroupConfig).filter_by(group_id=entry["group_id"]).first()
        webhook_url = group.group_robot_webhook_url if group else None
        if not webhook_url:
            return
        direction_label = "入库" if direction == "inbound" else "出库"
        context.setdefault("_deferred_webhook_notifications", []).append({
            "webhook_url": webhook_url,
            "content": (
                f"✅ 您的{direction_label}申请已完成\n"
                f"申请编号：{entry['serial_number']}\n"
                f"如有问题请联系管理员。"
            ),
        })
