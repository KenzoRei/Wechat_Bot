from handlers.label.fedex import FedExLabelHandler
from handlers.label.ups import UPSLabelHandler
from handlers.oms_record import OMSRecordHandler
from handlers.oms_create_workorder import OMSCreateWorkorderHandler
from handlers.reply_wechat import ReplyWeChatHandler

# Maps step_type strings (stored in workflow_step.step_type) to handler classes.
# Every step_type used in seed/migration SQL must have an entry here.
# If a step_type is missing, workflow_engine raises RuntimeError at runtime.

HANDLER_REGISTRY: dict[str, type] = {
    "create_fedex_label":  FedExLabelHandler,
    "create_ups_label":    UPSLabelHandler,
    "oms_record":          OMSRecordHandler,          # legacy no-op
    "oms_create_workorder": OMSCreateWorkorderHandler, # V6+
    "reply_wechat":        ReplyWeChatHandler,
}
