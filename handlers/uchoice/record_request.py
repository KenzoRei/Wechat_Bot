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
        # executing. A caller with warehouse_codes=None is genuinely
        # unscoped and never blocked here.
        allowed = context.get("warehouse_codes")
        requested = (context.get("collected_fields") or {}).get("warehouse_code")
        if allowed is not None and requested and requested not in allowed:
            raise RuntimeError("该仓库不在您的权限范围内。")
        return {}
