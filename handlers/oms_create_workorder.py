import base64
import logging
from uuid import UUID
from handlers.base import BaseHandler
from clients.oms_client import query_outbound_order, create_work_order
from core import customer_directory
from models.customer import LabelShipment

logger = logging.getLogger(__name__)


class OMSCreateWorkorderHandler(BaseHandler):
    """
    Creates an OMS work order after a label has been generated (FedEx or
    UPS -- carrier-agnostic, both workflows now include this step).

    oms_outbound_order_no is an optional field on fedex_label/ups_label —
    this handler branches on whether the customer provided it.

    Case A — no OMS order number:
        Creates a plain work order with no association.
        thirdNo = serial_number (bot's own reference)

    Case B — OMS order number provided:
        Queries the OMS outbound order to get its whCode.
        Creates a work order with associatedTrackingNoType=2 and
        associatedTrackingNo=oms_outbound_order_no, linking the two records.
        thirdNo = oms_outbound_order_no
        If the lookup can't find the order, falls back to Case A's shape
        (unlinked, thirdNo=serial_number) but preserves the customer-
        supplied order number in the work order's remark, rather than
        either dropping it or falsely claiming a verified link.

    OMS is customer-driven, not carrier-driven, and failure-tolerant: the
    label has already been created and paid for by the time this step
    runs, so nothing here may abort the turn's transaction -- every
    failure mode (no credentials configured, no warehouse code configured,
    an actual OMS API failure) is caught and recorded, never raised. See
    docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
    §3.3 for why catching (not raising) here is load-bearing, not just
    cleaner error handling: this codebase commits a whole turn in one
    transaction, so an uncaught exception here would roll back the label
    that already succeeded.

    Result keys:
        oms_work_order — set on any successful creation (linked or
            unlinked); None if OMS was skipped or failed.
        oms_error — set only when OMS was attempted and failed (missing
            wh_code, or the create_work_order call itself failing). None +
            oms_work_order None together mean "skipped, no credentials".
        oms_order_linked — present only for a successfully linked Case B.

    Credentials/config come from customer_credential/customer (the
    resolved request's billing_customer_id) — NOT group_service.config;
    that cutover is a hard, sequenced prerequisite (see the plan's §3.4),
    not a fallback.
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        fields          = context.get("collected_fields", {})
        result          = context.get("result", {})

        tracking_number = result.get("tracking_number", "")
        billing_customer_id = fields.get("billing_customer_id")

        if not tracking_number:
            raise RuntimeError("tracking_number missing — label step must run before oms_create_workorder")
        if not billing_customer_id:
            raise RuntimeError("billing_customer_id missing — should have been resolved before this step")

        oms_result = self._run_oms(context, fields, db, tracking_number, billing_customer_id)
        self._record_label_shipment(context, db, billing_customer_id, tracking_number, oms_result)
        return oms_result

    def _run_oms(self, context: dict, fields: dict, db, tracking_number: str, billing_customer_id: str) -> dict:
        serial_number = context.get("serial_number", "")
        oms_order_no  = (fields.get("oms_outbound_order_no") or "").strip()

        if not customer_directory.has_credentials(db, billing_customer_id, "oms_app_key", "oms_app_secret"):
            logger.info("No OMS credentials configured for customer %s -- skipping OMS push", billing_customer_id)
            return {"oms_work_order": None, "oms_error": None}

        creds = customer_directory.get_credentials(db, billing_customer_id)
        app_key    = creds["oms_app_key"]
        app_secret = creds["oms_app_secret"]

        customer = customer_directory.get_customer(db, billing_customer_id)
        wh_code = customer.oms_wh_code if customer else None
        if not wh_code:
            logger.info("oms_wh_code not configured for customer %s -- skipping OMS push", billing_customer_id)
            return {"oms_work_order": None, "oms_error": "oms_wh_code not configured for this customer"}

        # ── Case B: OMS order number provided ────────────────────────────────
        unmatched_note = ""
        if oms_order_no:
            try:
                order   = query_outbound_order(oms_order_no, app_key, app_secret)
                wh_code = order.get("whCode") or wh_code
                logger.info("OMS order found: %s  whCode=%s", oms_order_no, wh_code)
                try:
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
                    return {"oms_work_order": work_order_no, "oms_order_linked": oms_order_no}
                except Exception as exc:
                    logger.warning("OMS create_work_order failed (linked to %s): %s", oms_order_no, exc)
                    return {"oms_work_order": None, "oms_error": str(exc)}
            except Exception as exc:
                # Order not found (RuntimeError from query_outbound_order's
                # own business logic) OR a genuine transport failure
                # (requests.Timeout, ConnectionError, HTTPError from
                # _post()'s raise_for_status()) -- both must degrade the
                # same way, not just the "not found" business case. Falls
                # through to Case A's shape below, preserving the raw
                # customer-supplied order number in the remark instead of
                # either dropping it or falsely claiming a verified link.
                # If OMS is genuinely unreachable, the create_work_order
                # attempt below will itself fail and be caught by that
                # call's own except, ending in a recorded oms_error rather
                # than a second uncaught exception.
                logger.info("OMS order lookup for %s failed (%s) — creating unlinked work order with note in remark", oms_order_no, exc)
                unmatched_note = oms_order_no

        # ── Case A: no OMS order number, or Case B fallback after a lookup miss ─
        try:
            work_order_no = create_work_order(
                third_no=serial_number,
                wh_code=wh_code,
                tracking_number=tracking_number,
                collected_fields=fields,
                app_key=app_key,
                app_secret=app_secret,
                # associated fields intentionally omitted
                unmatched_oms_order_no=unmatched_note,
            )
            logger.info("OMS work order created: %s  (no linked order)", work_order_no)
            return {"oms_work_order": work_order_no}
        except Exception as exc:
            logger.warning("OMS create_work_order failed (unlinked): %s", exc)
            return {"oms_work_order": None, "oms_error": str(exc)}

    def _record_label_shipment(self, context: dict, db, billing_customer_id: str, tracking_number: str, oms_result: dict) -> None:
        """
        Companion ledger row (docs/reviews/active/2026-09-customer-service-
        and-label-pipeline/plan.md §2) -- one per label, linked 1:1 to
        request_log, regardless of OMS outcome (skipped/succeeded/failed
        all still get a row; only oms_work_order/oms_error differ).
        sales_amount comes from YiDiDa's separate /price quote (queried by
        handlers/label/base.py, non-fatally) -- None here means that quote
        failed, not that it was never attempted; label creation itself
        never depends on it.
        """
        request_log_id = context.get("request_log_id")
        if not request_log_id:
            logger.warning("No request_log_id in context -- cannot record label_shipment for tracking %s", tracking_number)
            return
        # Set by handlers/label/base.py's own result (context["result"]
        # accumulates across steps -- see workflow_engine.py/kefu_turn_apply.py's
        # context["result"].update(step_result)), never re-derived here.
        label_result = context.get("result", {})
        carrier = label_result.get("carrier")
        if not carrier:
            raise RuntimeError("carrier missing from context[\"result\"] -- label step must set it")
        label_base64 = label_result.get("label_base64")
        shipment = LabelShipment(
            request_log_id=UUID(request_log_id),
            billing_customer_id=billing_customer_id,
            carrier=carrier,
            tracking_number=tracking_number,
            oms_work_order=oms_result.get("oms_work_order"),
            oms_error=oms_result.get("oms_error"),
            sales_amount=label_result.get("sales_amount"),
            # Stored once, here, at creation time -- see the column's own
            # docstring (models/customer.py) for why this must never be
            # re-derived by calling YiDiDa's create_label a second time.
            label_pdf=base64.b64decode(label_base64) if label_base64 else None,
            status="created",
        )
        db.add(shipment)
        db.flush()
