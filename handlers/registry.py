from handlers.label.fedex import FedExLabelHandler
from handlers.label.ups import UPSLabelHandler
from handlers.oms_record import OMSRecordHandler
from handlers.reply_wechat import ReplyWeChatHandler

# Maps step_type strings (stored in workflow_step.step_type) to handler classes.
# Every step_type used in V2__seed_data.sql must have an entry here.
# If a step_type is missing, workflow_engine raises RuntimeError at runtime.

HANDLER_REGISTRY: dict[str, type] = {
    "create_fedex_label": FedExLabelHandler,
    "create_ups_label":   UPSLabelHandler,
    "oms_record":         OMSRecordHandler,
    "reply_wechat":       ReplyWeChatHandler,
}
