from handlers.base import BaseHandler


class QueryStorageHandler(BaseHandler):
    """
    view_storage — requires_confirmation=false, executes immediately.

    input_schema declares optional warehouse_code/sku_code fields, but this
    handler previously ignored collected_fields entirely and always showed
    every warehouse's full inventory. Now reads warehouse_code: an explicit
    value (already checked against the caller's own assignment by
    core.pre_confirm_validators._valid_caller_warehouse_scope) filters to
    that one warehouse; with none given, a warehouse-restricted caller
    (context["warehouse_codes"] is not None) sees only their own assigned
    warehouse(s) -- IN, not a single value, since one warehouseman can now
    cover several -- while a genuinely unscoped caller (customer/admin/
    accountant) continues to see every warehouse, unchanged.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.uchoice import UchoiceStorage

        fields = context.get("collected_fields", {})
        requested_warehouse = fields.get("warehouse_code")
        allowed_warehouses = context.get("warehouse_codes")

        query = db.query(UchoiceStorage).filter(UchoiceStorage.pallet_count > 0)
        if requested_warehouse:
            query = query.filter(UchoiceStorage.warehouse_code == requested_warehouse)
        elif allowed_warehouses is not None:
            query = query.filter(UchoiceStorage.warehouse_code.in_(allowed_warehouses))

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
    """
    view_storage_history — requires_confirmation=false, executes immediately.

    The chat reply (core/result_message.py) caps its detail list to the
    latest 10 movements -- WeCom Kefu's hard 2048-UTF-8-byte send_text
    limit made an uncapped reply for any range with real activity a 100%
    silent delivery failure. export_detail (optional field, V18 migration)
    lets a customer explicitly ask for the complete record instead,
    delivered as a spreadsheet via _try_build_workbook_and_link below
    (Smart Robot) or export_detail_requested in the returned result
    (Kefu -- see core/kefu_turn_apply.py's query_storage_history branch,
    which attaches the same workbook as a native chat file since
    response_url can't carry one).
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core.uchoice_storage_history_export import query_storage_history_rows

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        start_month = fields.get("start_month", "")
        end_month = fields.get("end_month", start_month)

        rows, start, range_end = query_storage_history_rows(db, warehouse_code, start_month, end_month)

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
        result = {
            "history_rows": history_rows,
            "range_start": start.isoformat(),
            "range_end": range_end.isoformat(),
        }

        export_detail_requested = bool(fields.get("export_detail"))
        result["export_detail_requested"] = export_detail_requested
        # Smart Robot's own delivery is a download link in the text reply
        # (response_url can't carry a file). Kefu sends the workbook as a
        # native chat file instead, built by core/kefu_turn_apply.py's
        # query_storage_history branch off export_detail_requested above --
        # building a download link here too would put a raw, unrendered
        # markdown link string in Kefu's plain-text reply on top of the
        # actual file attachment.
        if export_detail_requested and context.get("source_channel") != "kefu":
            download_url = self._try_build_workbook_and_link(context, db, warehouse_code, start_month, end_month)
            if download_url:
                result["download_url"] = download_url

        return result

    @staticmethod
    def _try_build_workbook_and_link(context: dict, db, warehouse_code: str, start_month: str, end_month: str) -> str | None:
        """Smart Robot's own delivery: a short-lived download link embedded
        in the text reply, matching ComputeInvoiceHandler's identical
        pattern. Best-effort -- never allowed to fail the main text
        response."""
        try:
            import config
            from core.uchoice_storage_history_export import build_storage_history_workbook
            from core.download_tokens import create_token

            data = build_storage_history_workbook(db, warehouse_code, start_month, end_month)
            filename = f"storage_history_{warehouse_code}_{start_month}_{end_month or start_month}.xlsx"
            token = create_token(
                data, filename,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            base_url = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")
            return f"{base_url}/files/download/{token}"
        except Exception as e:
            # Same non-fatal contract as ComputeInvoiceHandler's identical
            # helper -- a DB-level failure here must not abort the
            # already-successful text reply, and must not leave this
            # session's transaction aborted for the step after it.
            db.rollback()
            print(f"[uchoice] storage history workbook build failed (non-fatal): {e}", flush=True)
            return None


class ComputeInvoiceHandler(BaseHandler):
    """
    view_invoice — requires_confirmation=false, executes immediately.

    Also builds the detailed Excel workbook and attaches a short-lived
    download link (result["download_url"]) so the text reply itself can
    include it — response_url (the private per-message reply channel)
    doesn't support file messages at all, but it does support markdown
    links, so this is the only way to hand the requester the file
    privately. Additionally pushes the same workbook to the group's
    group_robot_webhook_url as a whole-group broadcast, if configured —
    kept as a secondary option, not the primary delivery path anymore.
    Both are best-effort: never allowed to fail the main text response.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from core.uchoice_invoice import compute_invoice

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        start_month = fields.get("start_month")
        end_month = fields.get("end_month")

        invoice = compute_invoice(db, warehouse_code, start_month, end_month)
        result = {k: (str(v) if hasattr(v, "quantize") else v) for k, v in invoice.items()}

        download_url = self._try_build_workbook_and_link(context, db, warehouse_code, start_month, end_month)
        if download_url:
            result["download_url"] = download_url
        return result

    @staticmethod
    def _try_build_workbook_and_link(context: dict, db, warehouse_code: str, start_month: str, end_month: str) -> str | None:
        try:
            import config
            from core.uchoice_invoice_export import build_invoice_workbook
            from core.download_tokens import create_token

            data = build_invoice_workbook(db, warehouse_code, start_month, end_month)
            filename = f"invoice_{warehouse_code}_{start_month}_{end_month or start_month}.xlsx"
            token = create_token(
                data, filename,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            base_url = getattr(config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")

            ComputeInvoiceHandler._try_push_workbook(context, db, data, filename)

            return f"{base_url}/files/download/{token}"
        except Exception as e:
            # A DB-level failure here (e.g. a bad query) leaves the session
            # in an aborted-transaction state — without rolling back, every
            # subsequent query on this same session (including the
            # reply_wechat step right after this one) would fail too, turning
            # a "best-effort extra" into a hard failure of the whole request.
            db.rollback()
            print(f"[uchoice] invoice workbook build failed (non-fatal): {e}", flush=True)
            return None

    @staticmethod
    def _try_push_workbook(context: dict, db, data: bytes, filename: str) -> None:
        try:
            from models.group import GroupConfig
            from clients.wechat_client import send_group_webhook_file

            group_id = context.get("group_id")
            group = db.query(GroupConfig).filter_by(group_id=group_id).first() if group_id else None
            webhook_url = group.group_robot_webhook_url if group else None
            if not webhook_url:
                return
            send_group_webhook_file(webhook_url, data, filename)
        except Exception as e:
            db.rollback()
            print(f"[uchoice] invoice workbook group push failed (non-fatal): {e}", flush=True)


class QueryPendingDigestHandler(BaseHandler):
    """
    view_pending_digest — requires_confirmation=false, executes immediately.

    Pull rather than push: render on demand the same pending-request digest
    that jobs/uchoice_daily.py sends on a
    schedule, but on-demand, for whoever asks. Scoped to the caller's own
    group_id (context["group_id"]) so it never leaks another tenant's
    pending requests; this naturally also scopes it correctly once Kefu is
    wired in, since a Kefu-originated request's group_id is the same
    customer group concept, not a separate identity space. Deliberately
    read-only -- unlike the scheduled job, this does not retire anything to
    status='stale' (a read query shouldn't have a side effect a customer/
    staff member didn't ask for).
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from datetime import datetime, timezone
        from models.request_log import RequestLog
        from models.service import ServiceType
        from core.uchoice_constants import STALE_THRESHOLD_DAYS

        group_id = context.get("group_id")
        service_type_ids = [
            st.service_type_id for st in
            db.query(ServiceType).filter(
                ServiceType.name.in_(["uchoice_inbound_request", "uchoice_outbound_request"])
            ).all()
        ]
        if not service_type_ids or not group_id:
            return {"pending_rows": []}

        rows = (
            db.query(RequestLog)
            .filter(
                RequestLog.status == "processing",
                RequestLog.service_type_id.in_(service_type_ids),
                RequestLog.group_id == group_id,
            )
            .order_by(RequestLog.created_at.asc())
            .all()
        )

        now = datetime.now(timezone.utc)
        pending_rows = []
        for log in rows:
            days = (now - log.created_at).days
            pending_rows.append({
                "serial_number": log.serial_number,
                "days_pending": days,
                "past_threshold": days >= STALE_THRESHOLD_DAYS - 1,
            })
        return {"pending_rows": pending_rows}


class ExplainServiceHandler(BaseHandler):
    """
    explain_service — requires_confirmation=false, executes immediately.

    Looks up the matched service_type by name and returns its stored
    description/keywords untouched — the AI's job (in prompt_builder.py) is
    only to identify WHICH service the user is asking about, never to author
    or paraphrase the explanation itself.

    Scoped to services actually granted to the caller's own group (via
    context["allowed_services"], the same deny-by-default list every other
    service already respects) — NOT a global service_type lookup. This
    platform is multi-tenant (each WeCom group belongs to a different
    client); an unscoped lookup let one client's group learn that another
    client's services (e.g. fedex_label/ups_label) exist at all, found live
    when a U-Choice group's admin could see a different client's label
    services. Checked here too, not just in the candidate list the AI
    matches against, so a literal exact-name guess can't bypass it.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.service import ServiceType

        fields = context.get("collected_fields", {})
        target_name = fields.get("target_service_name")

        allowed_names = {s["name"] for s in context.get("allowed_services", [])}
        service = None
        if target_name and target_name in allowed_names:
            service = db.query(ServiceType).filter_by(name=target_name, is_active=True).first()
        if service is None:
            return {"found": False, "target_service_name": target_name}

        return {
            "found": True,
            "target_service_name": service.name,
            "service_description": service.description or "",
            "service_keywords": service.keywords or [],
        }
