from handlers.base import BaseHandler


def _artifact_key(request_log_id, doc_type: str) -> str:
    """
    kefu-migration-plan.md Sec 11.2: stable idempotency identity -- the same
    logical document (same request, same doc_type) always produces the same
    key, so a regenerated artifact for a deferred Kefu delivery is
    recognizable as "the same document" rather than a new one.
    """
    return f"{request_log_id}:{doc_type}"


def _wrap_artifact_for_smart_robot(artifact: dict) -> dict:
    """
    kefu-migration-plan.md Sec 4: the handler itself only produces a
    channel-neutral Artifact (bytes/filename/content_type/artifact_key);
    wrapping it into a short-lived download token is Smart Robot's own
    delivery mechanism, unchanged from before this refactor. Kefu's path
    (not yet wired -- Codex's transport, per Sec 11.2/11.3) uploads the same
    Artifact via its own media API instead of calling this.
    """
    import config as app_config
    from core.download_tokens import create_token

    token = create_token(artifact["bytes"], artifact["filename"], content_type=artifact["content_type"])
    base_url = getattr(app_config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")
    return {
        "pdf_url": f"{base_url}/files/download/{token}",
        "pdf_status": "ready",
        "pdf_artifact_key": artifact["artifact_key"],
    }


class GeneratePdfStubHandler(BaseHandler):
    """
    confirm_inbound_completion's doc_type ("receiving_confirmation") stays
    stubbed — that's a separate, later feature. confirm_outbound_completion's
    doc_type ("delivery_confirmation") is real: builds the TWF-style delivery
    order PDF as a channel-neutral artifact, currently delivered via a
    short-lived download link (Smart Robot's own wrapping, see
    _wrap_artifact_for_smart_robot above).
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        doc_type = config.get("doc_type", "confirmation")
        if doc_type == "outbound_instruction":
            artifact = self._build_outbound_instruction_artifact(context, db)
        elif doc_type == "delivery_confirmation":
            # Legacy completion-time path -- retained only in case an old
            # deployed workflow_step row still references it; Phase 3's
            # migration removes this step from confirm_outbound_completion
            # going forward. New requests use "outbound_instruction" above.
            artifact = self._build_delivery_order_artifact(context, db)
        else:
            print(f"[uchoice] PDF generation stubbed (doc_type={doc_type}) — not yet implemented", flush=True)
            return {"pdf_url": None, "pdf_status": "pending"}

        if artifact is None:
            return {"pdf_url": None, "pdf_status": "failed"}
        return _wrap_artifact_for_smart_robot(artifact)

    @staticmethod
    def _build_outbound_instruction_artifact(context: dict, db) -> dict | None:
        """
        Phase 3 (phase3-outbound-pdf-timing.md): the pickup/delivery
        instruction PDF, generated once at outbound-request creation time --
        not at warehouse completion. Runs in the post-commit side-effect
        phase (core/workflow_engine.py's _SIDE_EFFECT_STEP_TYPES), so a
        failure here must never roll back or relabel the already-successful
        request; every path below returns cleanly rather than raising.

        Data source is the request's OWN validated collected_fields (what
        was requested), never a completion-time structure like
        fulfillment_lines (what was actually shipped) -- those don't exist
        yet at this point in the lifecycle.

        Logical idempotency: delivery_date is read from the persisted
        RequestLog.created_at (never datetime.now()), so calling this
        handler again for the same request -- e.g. a retry, or a Kefu
        deferred-delivery regeneration (plan Sec 11.3) -- produces
        content-identical output regardless of when the retry happens. See
        phase3-outbound-pdf-timing.md Sec 3.5 for why delivery-wrapper
        freshness (e.g. a new download token) isn't a second logical
        document.
        """
        try:
            from models.request_log import RequestLog
            from models.uchoice import UchoiceAddress
            from core.uchoice_context import sku_label_map
            from core.uchoice_delivery_order import build_delivery_order_pdf

            fields = context.get("collected_fields", {})
            serial_number = context.get("serial_number", "")
            request_log_id = context.get("request_log_id")

            log = db.query(RequestLog).filter_by(log_id=request_log_id).first() if request_log_id else None
            delivery_date = (log.created_at if log else None)
            if delivery_date is None:
                # No persisted timestamp available -- do not fall back to
                # datetime.now() (that's exactly the non-idempotent behavior
                # this phase removes). Fail this step cleanly instead.
                print(f"[uchoice] outbound instruction PDF skipped: no RequestLog.created_at for {serial_number}", flush=True)
                return None
            delivery_date = delivery_date.date()

            consignee_company, consignee_addr = "", ""
            destination_address_id = fields.get("destination_address_id")
            if destination_address_id:
                addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
                if addr:
                    consignee_company, consignee_addr = addr.company_name or "", addr.addr

            sku_labels = sku_label_map(db)
            product_lines = []
            for line in fields.get("sku_lines", []) or []:
                label = sku_labels.get(line.get("sku_code"), line.get("sku_code", "?"))
                if "box_count" in line:
                    qty = line["box_count"]
                    product_lines.append({"description": label, "quantity_text": f"Quantity: {qty:,}"})
                else:
                    bpp = line.get("boxes_per_pallet", 0) or 0
                    pallets = line.get("pallet_count", 0) or 0
                    qty = bpp * pallets
                    product_lines.append({
                        "description": label,
                        "quantity_text": f"{bpp:,} Units per Plt x {pallets:,} Plts = {qty:,}",
                    })

            pdf_bytes = build_delivery_order_pdf(
                consignee_company=consignee_company,
                consignee_addr=consignee_addr,
                delivery_date=delivery_date,
                product_lines=product_lines,
                outbound_order_no=serial_number,
            )
            return {
                "bytes": pdf_bytes,
                "filename": f"Delivery_Order_{serial_number}.pdf",
                "content_type": "application/pdf",
                "artifact_key": _artifact_key(request_log_id, "outbound_instruction"),
            }
        except Exception as e:
            print(f"[uchoice] outbound instruction PDF generation failed (non-fatal): {e}", flush=True)
            return None

    @staticmethod
    def _build_delivery_order_artifact(context: dict, db) -> dict | None:
        try:
            from datetime import datetime, timezone
            from models.uchoice import UchoiceAddress
            from core.uchoice_context import sku_label_map
            from core.uchoice_delivery_order import build_delivery_order_pdf

            target = context.get("_uchoice_target", {})
            original_fields = target.get("original_fields", {})
            serial_number = target.get("serial_number", "")
            request_log_id = context.get("request_log_id")
            fulfillment_lines = context.get("result", {}).get("fulfillment_lines", [])

            consignee_company, consignee_addr = "", ""
            destination_address_id = original_fields.get("destination_address_id")
            if destination_address_id:
                addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
                if addr:
                    consignee_company, consignee_addr = addr.company_name or "", addr.addr

            sku_labels = sku_label_map(db)
            product_lines = []
            for line in fulfillment_lines:
                label = sku_labels.get(line.get("sku_code"), line.get("sku_code", "?"))
                if "convert" in line:
                    # loose/partial-pallet pick — per user: render as the
                    # total pallet count for this line, which is always 1
                    # (apply_storage_delta always applies convert_in at
                    # delta=1 — see handlers/uchoice/storage_txns.py).
                    product_lines.append({"description": label, "quantity_text": "Quantity: 1"})
                else:
                    bpp = line.get("boxes_per_pallet", 0)
                    pallets = line.get("pallet_count", 0)
                    qty = bpp * pallets
                    product_lines.append({
                        "description": label,
                        "quantity_text": f"{bpp:,} Units per Plt x {pallets:,} Plts = {qty:,}",
                    })

            pdf_bytes = build_delivery_order_pdf(
                consignee_company=consignee_company,
                consignee_addr=consignee_addr,
                delivery_date=datetime.now(timezone.utc).date(),
                product_lines=product_lines,
                outbound_order_no=serial_number,
            )
            return {
                "bytes": pdf_bytes,
                "filename": f"Delivery_Order_{serial_number}.pdf",
                "content_type": "application/pdf",
                "artifact_key": _artifact_key(request_log_id or serial_number, "delivery_confirmation"),
            }
        except Exception as e:
            # Best-effort — a PDF failure must never break the already-
            # successful storage mutation this step runs right after.
            print(f"[uchoice] delivery order PDF generation failed (non-fatal): {e}", flush=True)
            return None
