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

        lines = [
            f"{r.warehouse_code} {r.sku_code} @ {r.boxes_per_pallet}/托：{r.pallet_count} 托"
            for r in rows
        ]
        return {"storage_lines": lines}


class QueryStorageHistoryHandler(BaseHandler):
    """view_storage_history — requires_confirmation=false, executes immediately."""

    def handle(self, context: dict, config: dict, db) -> dict:
        import calendar
        from datetime import date, timedelta
        from models.uchoice import UchoiceStorageTxn

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        target_month = fields.get("target_month", "")
        year, month = (int(p) for p in target_month.split("-"))
        start = date(year, month, 1)
        end_exclusive = date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)

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

        lines = [
            f"{r.created_at.strftime('%m-%d %H:%M')} {r.txn_type} {r.sku_code}@{r.boxes_per_pallet} {r.pallet_delta:+d}"
            for r in rows
        ]
        return {"history_lines": lines}


class ComputeInvoiceHandler(BaseHandler):
    """view_invoice — requires_confirmation=false, executes immediately."""

    def handle(self, context: dict, config: dict, db) -> dict:
        from core.uchoice_invoice import compute_invoice

        fields = context.get("collected_fields", {})
        invoice = compute_invoice(db, fields.get("warehouse_code"), fields.get("target_month"))
        return {k: (str(v) if hasattr(v, "quantize") else v) for k, v in invoice.items()}
