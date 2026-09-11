import logging
import re
from datetime import datetime, timezone
from handlers.base import BaseHandler
from clients.yidida_client import create_label, get_price_quote, ShipmentRejected
from core import customer_directory
from core.workflow_errors import LabelCreationRejected

logger = logging.getLogger(__name__)


def _generate_ke_hu_dan_hao(context: dict, billing_customer_id: str) -> str:
    """
    Generates YiDiDa customer order number (客户单号).
    Format: ChatBot_<billing_customer_id>_<user8>_<YYYYMMDD>_<serial3>
    Example: ChatBot_F000225_Simon_20260429_005

    Uses billing_customer_id, not the WeCom group's own description --
    that field is arbitrary human-entered text scoped to a single WeCom
    group (e.g. "U-Choice main group"), and labels are no longer a
    U-Choice-only service: any customer, in any group, can now generate
    one. billing_customer_id is the one identity that's actually
    consistent across every customer and every group.
    """
    user = re.sub(r'\s+', '', context.get("display_name") or "")[:8]
    date = datetime.now(timezone.utc).strftime("%Y%m%d")

    serial_number = context.get("serial_number", "")
    serial_3 = serial_number.split("-")[-1][-3:] if serial_number else "001"

    return f"ChatBot_{billing_customer_id}_{user}_{date}_{serial_3}"


class YDDLabelBaseHandler(BaseHandler):
    """
    Shared label creation logic for all carriers via YiDiDa.
    FedExLabelHandler and UPSLabelHandler inherit from this —
    they only need to declare their carrier name.

    Credentials/config come from customer_credential/customer (the
    resolved request's billing_customer_id) — NOT group_service.config.
    That cutover is a hard, sequenced prerequisite (see
    docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
    §3.4), not a fallback: no group-level YiDiDa config is read here at
    all.

        ydd_username / ydd_password — the customer's own YiDiDa login
            credentials (customer_credential). ydd_username is USUALLY
            (not always -- documented exceptions exist) identical to the
            customer's own customer_id, but is always read from storage
            here, never derived from billing_customer_id.
        ydd_channel_id[carrier] — YiDiDa channel ID (customer.ydd_channel_id,
            a per-carrier dict — not a secret, plain profile config).
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        fields = context.get("collected_fields", {})

        carrier = config.get("carrier")
        billing_customer_id = fields.get("billing_customer_id")
        if not billing_customer_id:
            raise RuntimeError("billing_customer_id missing — should have been resolved before this step")

        # Revalidate here, at actual execution time, not just at field-
        # collection time -- collection and confirmation are separate
        # turns, and this is the last point before an irreversible external
        # call (YDD) and real money. context["role"]/
        # context["requester_billing_customer_id"] are freshly rebuilt for
        # THIS turn (build_context() reruns per turn), so this genuinely
        # catches: the customer being deactivated, or (for a customer-role
        # requester) their own binding having been reassigned, in the gap
        # between collection and confirmation -- not just re-checking the
        # same stale state twice.
        revalidated_id, revalidation_error = customer_directory.resolve_billing_customer_id(
            db, context.get("role") == "customer", context.get("requester_billing_customer_id"), billing_customer_id,
        )
        if revalidation_error or revalidated_id != billing_customer_id:
            raise RuntimeError(
                f"billing_customer_id revalidation failed at execution time for {billing_customer_id!r}: "
                f"{revalidation_error or 'requester binding changed since collection'}"
            )

        creds = customer_directory.get_credentials(db, billing_customer_id)
        username = creds.get("ydd_username")
        password = creds.get("ydd_password")
        if not username or not password:
            raise RuntimeError(f"YiDiDa credentials (ydd_username/ydd_password) not configured for customer {billing_customer_id}")

        customer = customer_directory.get_customer(db, billing_customer_id)
        channel_id = (customer.ydd_channel_id or {}).get(carrier) if customer else None
        if not channel_id:
            raise RuntimeError(f"ydd_channel_id[{carrier!r}] not configured for customer {billing_customer_id}")

        # apply defaults for optional fields not provided by customer
        default_service_level = "FEDEX_GROUND" if carrier == "fedex" else "UPS_GROUND"

        api_fields = {
            "service_level": default_service_level,   # overridden if customer provided it
            **fields,
            "ydd_cust_id":      username,
            "ydd_channel_id":   channel_id,
            "ke_hu_dan_hao":    _generate_ke_hu_dan_hao(context, billing_customer_id),
        }

        try:
            result = create_label(carrier=carrier, fields=api_fields, api_key=password)
        except ShipmentRejected as exc:
            # A business rejection (bad phone, bad address, etc.), not an
            # infra failure -- translate into the shared, orchestration-
            # level exception so core/kefu_turn_apply.py and
            # core/workflow_engine.py can end this turn gracefully with a
            # real reply instead of it propagating as an unhandled
            # exception (previously: silent failure, no reply sent at all).
            raise LabelCreationRejected(exc.carrier_message) from exc

        # Price quote comes from YiDiDa's genuinely separate /price endpoint
        # -- /yundans (create_label, just above) carries no pricing field at
        # all. Non-fatal: the label has already been created (and, in
        # effect, paid for) by this point, so a quote failure must never
        # cost the customer their label -- same failure-tolerance principle
        # already applied to OMS (see handlers/oms_create_workorder.py).
        # sales_amount stays unset on failure; label_shipment still gets
        # written without it.
        try:
            price_result = get_price_quote(fields=api_fields, api_key=password)
            sales_amount = price_result["sales_amount"]
        except Exception as exc:
            logger.warning("YiDiDa price quote failed for customer %s (carrier=%s): %s", billing_customer_id, carrier, exc)
            sales_amount = None

        # result contains tracking_number and label_url; carrier/sales_amount
        # are added so downstream steps (oms_create_workorder's
        # label_shipment write) don't have to re-derive/re-fetch them,
        # which they don't otherwise receive. Stored in context["result"]
        # for downstream steps (oms_create_workorder, reply_wechat).
        return {**result, "carrier": carrier, "sales_amount": sales_amount}
