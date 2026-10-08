"""
cancel_inbound_request_batch / cancel_outbound_request_batch, step 1 (Kefu
only). Design: docs/ai-collaboration/2026-10-cancel-batch/plan.md.
"""
import traceback

from handlers.base import BaseHandler
from handlers.uchoice.complete_batch import _BatchBlocked


class RunCancellationBatchHandler(BaseHandler):
    """
    Cancels every confirmed request, all-or-nothing (decision D2), reusing
    the single cancel's steps unchanged per target:
      1. lock every target row in log_id order and re-check it
         (LookupAndValidateCancellationHandler) -- one lock class, one
         order, so overlapping batches serialize on the shared row instead
         of deadlocking;
      2. cancel each target in display order (CancelExistingRequestHandler);
      3. after the cancellation savepoint commits, notify each original
         requester (NotifyCancelledRequestHandler), each notice isolated in
         its own savepoint so no notice can block Kefu's outer commit
         (review R1).

    Steps 1-2 run inside one SAVEPOINT: Kefu can't plain-rollback, the same
    transaction holds the CaseExecution claim. A rejection rolls back to the
    savepoint, so no target changes, and returns _kefu_stop_workflow
    "batch_blocked"; core/kefu_turn_apply.py's _finish_batch_stop re-renders
    the summary without the blocked request.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        direction = config.get("direction")
        fields = context.get("collected_fields") or {}
        serials = list(fields.get("reference_serials") or [])
        indices = context.get("_batch_confirm_indices")
        confirmed = [serials[i - 1] for i in indices if 1 <= i <= len(serials)] if indices else serials

        savepoint = db.begin_nested()
        try:
            cancelled = self._run(db, context, direction, confirmed)
        except _BatchBlocked as blocked:
            savepoint.rollback()
            return {"_kefu_stop_workflow": "batch_blocked", "batch_blocked": {
                "kind": blocked.kind, "serials": blocked.serials, "message": blocked.message,
                "target_status": blocked.target_status, "confirmed": confirmed,
            }}
        except Exception:
            savepoint.rollback()
            print("[cancel_batch] unexpected failure, batch rolled back to savepoint:", flush=True)
            traceback.print_exc()
            return {"_kefu_stop_workflow": "batch_failed"}
        savepoint.commit()

        from handlers.uchoice.cancel_request import NotifyCancelledRequestHandler
        for entry in cancelled:
            NotifyCancelledRequestHandler().handle(entry.pop("_sub"), {}, db)
        return {"batch_direction": direction, "batch_cancelled": cancelled}

    def _run(self, db, context: dict, direction: str, confirmed: list[str]) -> list[dict]:
        from core import completion_batch as cb
        from core.uchoice_context import _creator_names
        from core.workflow_errors import TargetOperationRejected
        from handlers.uchoice.cancel_request import CancelExistingRequestHandler, LookupAndValidateCancellationHandler

        targets = {}
        for serial in confirmed:
            target = cb.load_target(db, serial)
            if target is None:
                raise _BatchBlocked("rejected", [serial], f"{serial} 未找到该申请")
            targets[serial] = target

        subs = {}
        for serial in sorted(confirmed, key=lambda s: str(targets[s].log.log_id)):
            sub = self._sub_context(context, targets[serial])
            try:
                LookupAndValidateCancellationHandler().handle(sub, {"direction": direction}, db)
            except TargetOperationRejected as e:
                raise _BatchBlocked(
                    "rejected", [serial], e.user_message, getattr(e, "current_status", None),
                ) from e
            subs[serial] = sub

        creators = _creator_names(db, [targets[s].log for s in confirmed])
        cancelled = []
        for serial in confirmed:
            CancelExistingRequestHandler().handle(subs[serial], {}, db)
            log = targets[serial].log
            cancelled.append({
                "serial_number": serial,
                "warehouse_code": targets[serial].warehouse_code,
                "created_by_name": creators.get(log.log_id),
                "_sub": subs[serial],
            })
        return cancelled

    @staticmethod
    def _sub_context(context: dict, target) -> dict:
        return {
            "wechat_openid": context.get("wechat_openid"),
            "submitted_by_staff_id": context.get("submitted_by_staff_id"),
            "source_channel": context.get("source_channel"),
            "role": context.get("role"),
            "group_id": context.get("group_id"),
            "request_log_id": str(target.log.log_id),
            "serial_number": target.serial,
            "collected_fields": {},
            "result": {},
            # The same list object: a Smart Robot group notice deferred for
            # one target lands on the turn's own list, which Kefu flushes
            # after its commit (core/kefu_case_adapter.py).
            "_deferred_webhook_notifications": context.setdefault("_deferred_webhook_notifications", []),
        }
