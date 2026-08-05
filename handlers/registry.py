from handlers.label.fedex import FedExLabelHandler
from handlers.label.ups import UPSLabelHandler
from handlers.oms_create_workorder import OMSCreateWorkorderHandler
from handlers.reply_wechat import ReplyWeChatHandler

from handlers.uchoice.record_request import RecordUchoiceRequestHandler
from handlers.uchoice.lookup_validate import LookupAndValidateCompletionHandler
from handlers.uchoice.storage_txns import (
    ApplyInboundStorageHandler,
    ApplyOutboundStorageHandler,
    AdjustStorageHandler,
    RecountStorageHandler,
    MoveStorageHandler,
)
from handlers.uchoice.pdf_stub import GeneratePdfStubHandler
from handlers.uchoice.complete_request import CompleteExistingRequestHandler
from handlers.uchoice.queries import QueryStorageHandler, QueryStorageHistoryHandler, ComputeInvoiceHandler, ExplainServiceHandler
from handlers.uchoice.address import UpsertAddressHandler
from handlers.uchoice.role_change import RoleChangeHandler

# Maps step_type strings (stored in workflow_step.step_type) to handler classes.
# Every step_type used in seed/migration SQL must have an entry here.
# If a step_type is missing, workflow_engine raises RuntimeError at runtime.

HANDLER_REGISTRY: dict[str, type] = {
    "create_fedex_label":   FedExLabelHandler,
    "create_ups_label":     UPSLabelHandler,
    "oms_create_workorder": OMSCreateWorkorderHandler,
    "reply_wechat":         ReplyWeChatHandler,

    "record_uchoice_request":         RecordUchoiceRequestHandler,
    "lookup_and_validate_completion": LookupAndValidateCompletionHandler,
    "apply_inbound_storage_txn":      ApplyInboundStorageHandler,
    "apply_outbound_storage_txn":     ApplyOutboundStorageHandler,
    "adjust_storage_txn":             AdjustStorageHandler,
    "recount_storage_txn":            RecountStorageHandler,
    "move_storage_txn":               MoveStorageHandler,
    "generate_pdf_stub":              GeneratePdfStubHandler,
    "complete_existing_request":      CompleteExistingRequestHandler,
    "query_storage":                  QueryStorageHandler,
    "query_storage_history":          QueryStorageHistoryHandler,
    "upsert_address":                 UpsertAddressHandler,
    "apply_role_change":              RoleChangeHandler,
    "compute_invoice_handler":        ComputeInvoiceHandler,
    "explain_service":                ExplainServiceHandler,
}
