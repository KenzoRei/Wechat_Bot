"""
Shared exception types for the workflow pipeline. Deliberately separate from
core/workflow_engine.py: raised by handlers (handlers/uchoice/*.py), which
cannot import from workflow_engine.py without a cycle (workflow_engine.py
imports HANDLER_REGISTRY, which imports those same handler modules), and
caught by two independent orchestration modules (core/workflow_engine.py for
Smart Robot, core/kefu_turn_apply.py for Kefu) that must not import this
type from one another either.
"""


class TargetOperationRejected(Exception):
    """
    Base for every rejection of a targets_existing_request operation
    (completion or cancellation) that must never mark the TARGET request
    itself failed -- only the attempting session/turn is closed out. The
    target is left exactly as it already was.

    This matters beyond the concurrency-race case
    (TargetAlreadyResolvedError below): a targets_existing_request
    handler's own validation checks (wrong direction, wrong group, not
    authorized) previously raised a bare RuntimeError, which the shared
    exception handling in core/workflow_engine.py/core/kefu_turn_apply.py
    treats as an operational failure and calls mark_failed() on
    session.request_log_id -- the TARGET, not this session's own row. A
    caller referencing the wrong serial number (e.g. an outbound serial
    typed into a cancel_inbound_request turn) would then incorrectly mark
    that unrelated, perfectly valid target request 'failed'. Every such
    rejection must use a TargetOperationRejected subclass instead.
    """
    user_message: str


class TargetAlreadyResolvedError(TargetOperationRejected):
    """
    Raised by a locked lookup handler (LookupAndValidateCompletionHandler,
    LookupAndValidateCancellationHandler) when the target request_log's
    locked, refreshed status is no longer 'processing' -- a losing race
    against a concurrent completion/cancellation attempt on the same row.
    """

    def __init__(self, current_status: str, serial_number: str):
        self.current_status = current_status
        self.serial_number = serial_number
        self.user_message = f"申请 {serial_number} 当前状态为「{current_status}」，操作未执行。"
        super().__init__(self.user_message)


class TargetValidationError(TargetOperationRejected):
    """
    Raised by a targets_existing_request handler for a rejection that is
    NOT a concurrency race -- wrong direction, wrong group, not authorized,
    or the target itself doesn't resolve. Carries a plain user-facing
    message.
    """

    def __init__(self, message: str):
        self.user_message = message
        super().__init__(message)
