from handlers.base import BaseHandler


class QueryStorageHandler(BaseHandler):
    """view_storage — requires_confirmation=false, executes immediately."""

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.uchoice import UchoiceStorage

        fields = context.get("collected_fields", {})
        query = db.query(UchoiceStorage)
        if fields.get("warehouse_code"):
            query = query.filter_by(warehouse_code=fields["warehouse_code"])
        if fields.get("sku_code"):
            query = query.filter_by(sku_code=fields["sku_code"])
        rows = query.order_by(
            UchoiceStorage.warehouse_code, UchoiceStorage.sku_code, UchoiceStorage.boxes_per_pallet
        ).all()

        # structured, not pre-formatted — core/result_message.py's builder
        # resolves sku_code -> product name and formats for display
        storage_rows = [
            {
                "warehouse_code":   r.warehouse_code,
                "sku_code":         r.sku_code,
                "boxes_per_pallet": r.boxes_per_pallet,
                "pallet_count":     r.pallet_count,
            }
            for r in rows
        ]
        return {"storage_rows": storage_rows}


class QueryStorageHistoryHandler(BaseHandler):
    """view_storage_history — requires_confirmation=false, executes immediately."""

    def handle(self, context: dict, config: dict, db) -> dict:
        import calendar
        from datetime import date, timedelta, timezone, datetime
        from models.uchoice import UchoiceStorageTxn

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")

        start_month = fields.get("start_month", "")
        end_month = fields.get("end_month", start_month)
        start_year, start_mo = (int(p) for p in start_month.split("-"))
        end_year, end_mo = (int(p) for p in end_month.split("-"))
        start = date(start_year, start_mo, 1)
        month_end = date(end_year, end_mo, calendar.monthrange(end_year, end_mo)[1])

        # Cap the range at today when it reaches into the current, still-
        # in-progress month — there's no data past today regardless, but the
        # *displayed* range should say so rather than claiming a range that
        # hasn't happened yet.
        today = datetime.now(timezone.utc).date()
        range_end = min(month_end, today) if (end_year, end_mo) == (today.year, today.month) else month_end
        end_exclusive = range_end + timedelta(days=1)

        rows = (
            db.query(UchoiceStorageTxn)
            .filter(
                UchoiceStorageTxn.warehouse_code == warehouse_code,
                UchoiceStorageTxn.created_at >= start,
                UchoiceStorageTxn.created_at < end_exclusive,
            )
            .order_by(UchoiceStorageTxn.created_at)
            .all()
        )

        # structured, not pre-formatted — core/result_message.py's builder
        # resolves sku_code -> product name and txn_type -> Chinese label
        history_rows = [
            {
                "created_at":       r.created_at.isoformat() if r.created_at else None,
                "txn_type":         r.txn_type,
                "sku_code":         r.sku_code,
                "boxes_per_pallet": r.boxes_per_pallet,
                "pallet_delta":     r.pallet_delta,
            }
            for r in rows
        ]
        return {
            "history_rows": history_rows,
            "range_start": start.isoformat(),
            "range_end": range_end.isoformat(),
        }


class ComputeInvoiceHandler(BaseHandler):
    """
    view_invoice — requires_confirmation=false, executes immediately.

    Also pushes the detailed Excel workbook to the group's
    group_robot_webhook_url, if configured — response_url (the private
    per-message reply channel) doesn't support file messages at all, so
    there's no way to send the file privately to just the requester; this is
    a whole-group broadcast, same as the daily/monthly pushes. Silently
    skipped if the group has no webhook configured, and never allowed to
    fail the main text response — this is a best-effort extra, not a
    dependency of view_invoice actually working.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core.uchoice_invoice import compute_invoice

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        start_month = fields.get("start_month")
        end_month = fields.get("end_month")

        invoice = compute_invoice(db, warehouse_code, start_month, end_month)
        result = {k: (str(v) if hasattr(v, "quantize") else v) for k, v in invoice.items()}

        self._try_push_workbook(context, db, warehouse_code, start_month, end_month)
        return result

    @staticmethod
    def _try_push_workbook(context: dict, db, warehouse_code: str, start_month: str, end_month: str) -> None:
        try:
            from models.group import GroupConfig
            from core.uchoice_invoice_export import build_invoice_workbook
            from clients.wechat_client import send_group_webhook_file

            group_id = context.get("group_id")
            group = db.query(GroupConfig).filter_by(group_id=group_id).first() if group_id else None
            webhook_url = group.group_robot_webhook_url if group else None
            if not webhook_url:
                return

            data = build_invoice_workbook(db, warehouse_code, start_month, end_month)
            filename = f"invoice_{warehouse_code}_{start_month}_{end_month or start_month}.xlsx"
            send_group_webhook_file(webhook_url, data, filename)
        except Exception as e:
            # A DB-level failure here (e.g. a bad query) leaves the session
            # in an aborted-transaction state — without rolling back, every
            # subsequent query on this same session (including the
            # reply_wechat step right after this one) would fail too, turning
            # a "best-effort extra" into a hard failure of the whole request.
            db.rollback()
            print(f"[uchoice] invoice workbook push failed (non-fatal): {e}", flush=True)
