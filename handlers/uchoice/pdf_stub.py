from handlers.base import BaseHandler


class GeneratePdfStubHandler(BaseHandler):
    """
    confirm_inbound_completion's doc_type ("receiving_confirmation") stays
    stubbed — that's a separate, later feature. confirm_outbound_completion's
    doc_type ("delivery_confirmation") is real: builds the TWF-style delivery
    order PDF and returns a short-lived download link, reusing the same
    token mechanism as the invoice export link.
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        doc_type = config.get("doc_type", "confirmation")
        if doc_type == "delivery_confirmation":
            return self._generate_delivery_order(context, db)

        print(f"[uchoice] PDF generation stubbed (doc_type={doc_type}) — not yet implemented", flush=True)
        return {"pdf_url": None, "pdf_status": "pending"}

    @staticmethod
    def _generate_delivery_order(context: dict, db) -> dict:
        try:
            from datetime import datetime, timezone
            import config as app_config
            from models.uchoice import UchoiceAddress
            from core.uchoice_context import sku_label_map
            from core.uchoice_delivery_order import build_delivery_order_pdf
            from core.download_tokens import create_token

            target = context.get("_uchoice_target", {})
            original_fields = target.get("original_fields", {})
            serial_number = target.get("serial_number", "")
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
            filename = f"Delivery_Order_{serial_number}.pdf"
            token = create_token(pdf_bytes, filename, content_type="application/pdf")
            base_url = getattr(app_config, "SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")

            return {"pdf_url": f"{base_url}/files/download/{token}", "pdf_status": "ready"}
        except Exception as e:
            # Best-effort — a PDF failure must never break the already-
            # successful storage mutation this step runs right after.
            print(f"[uchoice] delivery order PDF generation failed (non-fatal): {e}", flush=True)
            return {"pdf_url": None, "pdf_status": "failed"}
