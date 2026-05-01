import logging
from handlers.base import BaseHandler
from clients.oms_client import query_outbound_order, create_work_order

logger = logging.getLogger(__name__)


class OMSCreateWorkorderHandler(BaseHandler):
    """
    Creates an OMS work order after a FedEx label has been generated.
    Used by BOTH fedex_label and fedex_oms_label services.

    Behaviour depends on whether oms_outbound_order_no was collected:

    Case A — no OMS order number (fedex_label service):
        Creates a plain work order with no association.
        thirdNo = serial_number (bot's own reference)

    Case B — OMS order number provided (fedex_oms_label service):
        Queries the OMS outbound order to get its whCode.
        Creates a work order with associatedTrackingNoType=2 and
        associatedTrackingNo=oms_outbound_order_no, linking the two records.
        thirdNo = oms_outbound_order_no

    config keys (from group_service.config):
        oms_app_key     — OMS App_Key
        oms_app_secret  — OMS App_Secret
        oms_wh_code     — warehouse code (used directly in Case A;
                          used as fallback in Case B if query returns none)
    """

    def handle(self, context: dict, config: dict) -> dict:
        fields          = context.get("collected_fields", {})
        result          = context.get("result", {})

        tracking_number = result.get("tracking_number", "")
        serial_number   = context.get("serial_number", "")
        oms_order_no    = (fields.get("oms_outbound_order_no") or "").strip()

        app_key    = config.get("oms_app_key", "")
        app_secret = config.get("oms_app_secret", "")
        wh_code    = config.get("oms_wh_code", "")

        if not app_key or not app_secret:
            raise RuntimeError("oms_app_key / oms_app_secret missing from group_service.config")
        if not tracking_number:
            raise RuntimeError("tracking_number missing — label step must run before oms_create_workorder")
        if not wh_code:
            raise RuntimeError("oms_wh_code missing from group_service.config")

        # ── Case B: OMS order number provided ────────────────────────────────
        if oms_order_no:
            try:
                order   = query_outbound_order(oms_order_no, app_key, app_secret)
                wh_code = order.get("whCode") or wh_code
                logger.info("OMS order found: %s  whCode=%s", oms_order_no, wh_code)
            except RuntimeError:
                # order not found — still create the work order with association
                logger.info("OMS order %s not found — creating work order with fallback whCode", oms_order_no)

            work_order_no = create_work_order(
                third_no=oms_order_no,
                wh_code=wh_code,
                tracking_number=tracking_number,
                collected_fields=fields,
                app_key=app_key,
                app_secret=app_secret,
                associated_tracking_no=oms_order_no,
                associated_tracking_no_type=2,
            )
            logger.info("OMS work order created: %s  (linked to %s)", work_order_no, oms_order_no)
            return {
                "oms_work_order":   work_order_no,
                "oms_order_linked": oms_order_no,
            }

        # ── Case A: no OMS order number ───────────────────────────────────────
        work_order_no = create_work_order(
            third_no=serial_number,
            wh_code=wh_code,
            tracking_number=tracking_number,
            collected_fields=fields,
            app_key=app_key,
            app_secret=app_secret,
            # associated fields intentionally omitted
        )
        logger.info("OMS work order created: %s  (no linked order)", work_order_no)
        return {
            "oms_work_order": work_order_no,
        }
