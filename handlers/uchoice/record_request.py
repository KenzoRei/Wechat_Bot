from handlers.base import BaseHandler


class RecordUchoiceRequestHandler(BaseHandler):
    """
    uchoice_inbound_request / uchoice_outbound_request — no-op. The
    request_log row already exists (created at new_request time) and already
    holds raw_message/collected context; there is nothing else to record at
    this point (storage only changes once the warehouse confirms physical
    completion). Kept as an explicit step for symmetry with the design doc
    and as a future extension point.
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        # Execution-time backstop, matching the pre-confirm
        # core.pre_confirm_validators._valid_caller_warehouse_scope check --
        # closes the gap between that check and this confirm-turn actually
        # executing. Uses core.role_policy.check_warehouse_scope --
        # fail-closed for a warehouse-scoped caller with no warehouse_codes
        # assigned, not the previous "codes is None means unrestricted"
        # inference.
        from core import role_policy
        requested = (context.get("collected_fields") or {}).get("warehouse_code")
        message = role_policy.check_warehouse_scope(context.get("role"), context.get("warehouse_codes"), requested)
        if message:
            raise RuntimeError(message)
        return {}
