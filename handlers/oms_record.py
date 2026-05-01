import logging
from handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class OMSRecordHandler(BaseHandler):
    """
    Legacy step type kept so the old fedex_with_oms / ups_with_oms workflow
    definitions in V2__seed_data.sql don't crash the handler registry lookup.

    This is a no-op — use oms_create_workorder for real OMS integration.
    """

    def handle(self, context: dict, config: dict) -> dict:
        logger.warning("oms_record step invoked — deprecated no-op, switch to oms_create_workorder")
        return {}
