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
        return {}
