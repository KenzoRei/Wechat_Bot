"""
Closed set of deterministic Kefu operational outcomes.

Every `KefuOutcome` is a frozen dataclass of already-resolved, display-ready
facts -- Chinese labels, counts, pre-built text blocks, serial numbers --
never raw database rows, ORM objects, or bare UUIDs meant for a later lookup.
This is deliberate: `core/kefu_response_renderer.py` renders from these
payloads alone and never touches the database. Orchestration constructs a
`KefuOutcome` from real
orchestration state (resolving labels via existing db-touching helpers like
`core/confirmation.py`/`core/result_message.py` first) and passes the
finished payload here.

Each payload validates its own required facts in `__post_init__` --
construction fails fast on missing/empty/contradictory data instead of
letting a malformed outcome reach the renderer. Expected business outcomes are
committed as data by the caller; unexpected exceptions never become one of
these; the caller, not this module, enforces the boundary between expected
outcomes and exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class OutcomeCode(Enum):
    """Every outcome this phase's renderer supports. No arbitrary strings."""
    # Routing
    SERVICE_LIST = auto()
    UNRECOGNIZED_REQUEST = auto()
    SERVICE_UNAVAILABLE = auto()
    # Access / case
    PERMISSION_DENIED = auto()
    NO_CASE = auto()
    CASE_CLOSED = auto()
    CASE_STALE = auto()
    # Collection
    MISSING_FIELDS = auto()
    INVALID_VALUE = auto()
    UNKNOWN_CATALOG_VALUE = auto()
    CONTRADICTORY_VALUE = auto()
    FIELD_CORRECTION_ACCEPTED = auto()
    # Candidate selection (generic, non-address)
    CANDIDATE_AMBIGUOUS = auto()
    CANDIDATE_NONE_ELIGIBLE = auto()
    # Address
    ADDRESS_AMBIGUOUS = auto()
    ADDRESS_PIVOT_UNAVAILABLE = auto()
    ADDRESS_PIVOT_STARTED = auto()
    # Inventory
    INSUFFICIENT_STOCK = auto()
    STOCK_CHANGED_AT_FULFILLMENT = auto()
    INVENTORY_INCONSISTENT = auto()
    # Confirmation
    CONFIRMATION_SUMMARY = auto()
    CONFIRMATION_CANCELLED = auto()
    CONFIRMATION_ALREADY_PROCESSED = auto()
    CONFIRMATION_NOTHING_PENDING = auto()
    CONFIRMATION_RECOVERING = auto()
    SESSION_CONFLICT = auto()
    # Execution
    EXECUTION_SUBMITTED = auto()
    EXECUTION_COMPLETED = auto()
    EXECUTION_RETRYABLE_FAILURE = auto()
    EXECUTION_PERMANENT_FAILURE = auto()
    # Query result framing
    QUERY_EMPTY = auto()
    QUERY_RESULT = auto()
    QUERY_INVALID_FILTERS = auto()
    # Completion notice
    COMPLETION_NOTICE = auto()
    # Semantic layer unavailable (provider exhaustion / invalid JSON)
    SEMANTIC_UNAVAILABLE = auto()


class KefuOutcomeError(ValueError):
    """Raised by a payload's __post_init__ when required facts are missing,
    empty, wrong-typed, or contradictory. Construction-time failure, never a
    renderer-time guess -- see module docstring."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise KefuOutcomeError(message)


@dataclass(frozen=True)
class FieldPrompt:
    """One missing/invalid field, already labeled for display."""
    field: str
    label: str
    question: str

    def __post_init__(self):
        _require(self.field, "FieldPrompt.field must be non-empty")
        _require(self.label, "FieldPrompt.label must be non-empty")
        _require(self.question, "FieldPrompt.question must be non-empty")


@dataclass(frozen=True)
class MissingFieldsOutcome:
    code = OutcomeCode.MISSING_FIELDS
    service_label: str
    fields: tuple[FieldPrompt, ...]

    def __post_init__(self):
        _require(self.service_label, "service_label must be non-empty")
        _require(len(self.fields) > 0, "fields must be non-empty")


@dataclass(frozen=True)
class InvalidValueOutcome:
    code = OutcomeCode.INVALID_VALUE
    field_label: str
    reason: str

    def __post_init__(self):
        _require(self.field_label, "field_label must be non-empty")
        _require(self.reason, "reason must be non-empty")


@dataclass(frozen=True)
class UnknownCatalogValueOutcome:
    code = OutcomeCode.UNKNOWN_CATALOG_VALUE
    field_label: str
    raw_value: str

    def __post_init__(self):
        _require(self.field_label, "field_label must be non-empty")
        _require(self.raw_value, "raw_value must be non-empty")


@dataclass(frozen=True)
class ContradictoryValueOutcome:
    code = OutcomeCode.CONTRADICTORY_VALUE
    field_label: str
    detail: str

    def __post_init__(self):
        _require(self.field_label, "field_label must be non-empty")
        _require(self.detail, "detail must be non-empty")


@dataclass(frozen=True)
class FieldCorrectionAcceptedOutcome:
    code = OutcomeCode.FIELD_CORRECTION_ACCEPTED
    field_label: str
    new_value_label: str

    def __post_init__(self):
        _require(self.field_label, "field_label must be non-empty")
        _require(self.new_value_label, "new_value_label must be non-empty")


@dataclass(frozen=True)
class CandidateOption:
    """One selectable candidate, already labeled for display -- never a bare
    ID; the renderer shows candidate_key only as an opaque selection token
    (e.g. a serial number), not a database identifier."""
    candidate_key: str
    label: str

    def __post_init__(self):
        _require(self.candidate_key, "candidate_key must be non-empty")
        _require(self.label, "label must be non-empty")


@dataclass(frozen=True)
class CandidateAmbiguousOutcome:
    code = OutcomeCode.CANDIDATE_AMBIGUOUS
    prompt: str
    options: tuple[CandidateOption, ...]

    def __post_init__(self):
        _require(self.prompt, "prompt must be non-empty")
        _require(len(self.options) >= 2, "options must have at least two entries")


@dataclass(frozen=True)
class CandidateNoneEligibleOutcome:
    code = OutcomeCode.CANDIDATE_NONE_ELIGIBLE
    explanation: str

    def __post_init__(self):
        _require(self.explanation, "explanation must be non-empty")


@dataclass(frozen=True)
class AddressOption:
    """A displayable address candidate -- company/address text only, never
    the raw address_id or customer_id."""
    candidate_key: str  # opaque per-turn selection token, not the DB id
    display_label: str  # e.g. "ABC 公司（123 Main St, City, ST 12345）"

    def __post_init__(self):
        _require(self.candidate_key, "candidate_key must be non-empty")
        _require(self.display_label, "display_label must be non-empty")


@dataclass(frozen=True)
class AddressAmbiguousOutcome:
    code = OutcomeCode.ADDRESS_AMBIGUOUS
    options: tuple[AddressOption, ...]

    def __post_init__(self):
        _require(len(self.options) >= 2, "options must have at least two entries")


@dataclass(frozen=True)
class AddressPivotUnavailableOutcome:
    code = OutcomeCode.ADDRESS_PIVOT_UNAVAILABLE
    escalation_note: str

    def __post_init__(self):
        _require(self.escalation_note, "escalation_note must be non-empty")


@dataclass(frozen=True)
class AddressPivotStartedOutcome:
    """Rendered ONLY after the caller's atomic pivot mutation has actually
    committed. The response may state that cancellation or pivot occurred only
    after the corresponding mutations are part of the successful commit; this
    outcome existing at all is
    itself evidence the pivot is real, never a claim ahead of the fact."""
    code = OutcomeCode.ADDRESS_PIVOT_STARTED
    cancelled_serial_number: str
    still_missing_fields: tuple[FieldPrompt, ...]

    def __post_init__(self):
        _require(self.cancelled_serial_number, "cancelled_serial_number must be non-empty")


@dataclass(frozen=True)
class StockShortage:
    sku_label: str
    requested_boxes: int
    available_boxes: int

    def __post_init__(self):
        _require(self.sku_label, "sku_label must be non-empty")
        _require(self.requested_boxes > 0, "requested_boxes must be positive")
        _require(self.available_boxes >= 0, "available_boxes must not be negative")
        _require(self.available_boxes < self.requested_boxes, "a shortage must actually be short")


@dataclass(frozen=True)
class InsufficientStockOutcome:
    code = OutcomeCode.INSUFFICIENT_STOCK
    warehouse_label: str
    shortages: tuple[StockShortage, ...]

    def __post_init__(self):
        _require(self.warehouse_label, "warehouse_label must be non-empty")
        _require(len(self.shortages) > 0, "shortages must be non-empty")


@dataclass(frozen=True)
class StockChangedOutcome:
    code = OutcomeCode.STOCK_CHANGED_AT_FULFILLMENT
    serial_number: str
    shortages: tuple[StockShortage, ...]

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")
        _require(len(self.shortages) > 0, "shortages must be non-empty")


@dataclass(frozen=True)
class InventoryInconsistentOutcome:
    code = OutcomeCode.INVENTORY_INCONSISTENT
    note: str

    def __post_init__(self):
        _require(self.note, "note must be non-empty")


@dataclass(frozen=True)
class ConfirmationSummaryOutcome:
    """Wraps an already-built confirmation summary (core/confirmation.py's
    existing build_confirmation_message output) -- this renderer family does
    not reconstruct confirmation text; it packages it as a deterministic
    summary."""
    code = OutcomeCode.CONFIRMATION_SUMMARY
    summary_text: str

    def __post_init__(self):
        _require(self.summary_text, "summary_text must be non-empty")


@dataclass(frozen=True)
class ConfirmationCancelledOutcome:
    code = OutcomeCode.CONFIRMATION_CANCELLED
    service_label: str
    serial_number: str = ""

    def __post_init__(self):
        _require(self.service_label, "service_label must be non-empty")


@dataclass(frozen=True)
class ConfirmationAlreadyProcessedOutcome:
    code = OutcomeCode.CONFIRMATION_ALREADY_PROCESSED


@dataclass(frozen=True)
class ConfirmationNothingPendingOutcome:
    code = OutcomeCode.CONFIRMATION_NOTHING_PENDING


@dataclass(frozen=True)
class ConfirmationRecoveringOutcome:
    """A concurrent confirmation attempt found the business mutation already
    durable (execution ledger 'db_committed') but the turn not yet finalized
    -- the original reply is genuinely unrecoverable, so this renders a safe,
    generic status instead of re-running the AI/workflow."""
    code = OutcomeCode.CONFIRMATION_RECOVERING


@dataclass(frozen=True)
class SessionConflictOutcome:
    """
    A staff member's message resolved to a genuinely different, distinct
    service than their currently open case -- rather than silently forcing
    it into that case (the original bug) or silently abandoning the open
    case, this asks the staff to explicitly decide. Stateless by design: no
    flag is persisted anywhere between this reply and the staff's next
    message. 取消/继续 are handled entirely by the ALREADY-existing cancel/
    continuation intents on the next turn -- this outcome only renders the
    prompt, it doesn't gate what happens next.
    """
    code = OutcomeCode.SESSION_CONFLICT
    service_label: str
    case_number: str
    last_question: str = ""

    def __post_init__(self):
        _require(self.service_label, "service_label must be non-empty")
        _require(self.case_number, "case_number must be non-empty")


@dataclass(frozen=True)
class ExecutionSubmittedOutcome:
    code = OutcomeCode.EXECUTION_SUBMITTED
    serial_number: str
    service_label: str

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")
        _require(self.service_label, "service_label must be non-empty")


@dataclass(frozen=True)
class ExecutionCompletedOutcome:
    code = OutcomeCode.EXECUTION_COMPLETED
    serial_number: str
    service_label: str
    result_lines: tuple[str, ...] = ()

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")
        _require(self.service_label, "service_label must be non-empty")


@dataclass(frozen=True)
class ExecutionRetryableFailureOutcome:
    code = OutcomeCode.EXECUTION_RETRYABLE_FAILURE
    service_label: str

    def __post_init__(self):
        _require(self.service_label, "service_label must be non-empty")


@dataclass(frozen=True)
class ExecutionPermanentFailureOutcome:
    code = OutcomeCode.EXECUTION_PERMANENT_FAILURE
    service_label: str
    reason: str

    def __post_init__(self):
        _require(self.service_label, "service_label must be non-empty")
        _require(self.reason, "reason must be non-empty")


@dataclass(frozen=True)
class PermissionDeniedOutcome:
    code = OutcomeCode.PERMISSION_DENIED
    reason_label: str

    def __post_init__(self):
        _require(self.reason_label, "reason_label must be non-empty")


@dataclass(frozen=True)
class NoCaseOutcome:
    code = OutcomeCode.NO_CASE


@dataclass(frozen=True)
class CaseClosedOutcome:
    code = OutcomeCode.CASE_CLOSED
    serial_number: str

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")


@dataclass(frozen=True)
class CaseStaleOutcome:
    code = OutcomeCode.CASE_STALE
    serial_number: str

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")


@dataclass(frozen=True)
class ServiceListEntry:
    """One service's short display label plus a couple of example trigger
    phrases -- lets check_services double as "how do I ask for this"
    documentation, not just a bare name."""
    label: str
    keywords: tuple[str, ...] = ()

    def __post_init__(self):
        _require(self.label, "ServiceListEntry.label must be non-empty")


@dataclass(frozen=True)
class ServiceListOutcome:
    code = OutcomeCode.SERVICE_LIST
    entries: tuple[ServiceListEntry, ...]

    def __post_init__(self):
        _require(len(self.entries) > 0, "entries must be non-empty")


@dataclass(frozen=True)
class UnrecognizedRequestOutcome:
    code = OutcomeCode.UNRECOGNIZED_REQUEST


@dataclass(frozen=True)
class ServiceUnavailableOutcome:
    code = OutcomeCode.SERVICE_UNAVAILABLE


@dataclass(frozen=True)
class QueryEmptyOutcome:
    code = OutcomeCode.QUERY_EMPTY
    query_label: str

    def __post_init__(self):
        _require(self.query_label, "query_label must be non-empty")


@dataclass(frozen=True)
class QueryResultOutcome:
    """Wraps an already-built result body (core/result_message.py's existing
    section builders) -- this renderer family packages, not reconstructs,
    the same as ConfirmationSummaryOutcome above."""
    code = OutcomeCode.QUERY_RESULT
    title: str
    body_text: str

    def __post_init__(self):
        _require(self.title, "title must be non-empty")
        _require(self.body_text, "body_text must be non-empty")


@dataclass(frozen=True)
class QueryInvalidFiltersOutcome:
    code = OutcomeCode.QUERY_INVALID_FILTERS
    reason: str

    def __post_init__(self):
        _require(self.reason, "reason must be non-empty")


@dataclass(frozen=True)
class CompletionNoticeOutcome:
    code = OutcomeCode.COMPLETION_NOTICE
    serial_number: str
    direction_label: str  # "入库" | "出库" only -- validated below

    def __post_init__(self):
        _require(self.serial_number, "serial_number must be non-empty")
        _require(self.direction_label in ("入库", "出库"), "direction_label must be 入库 or 出库")


@dataclass(frozen=True)
class SemanticUnavailableOutcome:
    code = OutcomeCode.SEMANTIC_UNAVAILABLE


KefuOutcome = (
    MissingFieldsOutcome
    | InvalidValueOutcome
    | UnknownCatalogValueOutcome
    | ContradictoryValueOutcome
    | FieldCorrectionAcceptedOutcome
    | CandidateAmbiguousOutcome
    | CandidateNoneEligibleOutcome
    | AddressAmbiguousOutcome
    | AddressPivotUnavailableOutcome
    | AddressPivotStartedOutcome
    | InsufficientStockOutcome
    | StockChangedOutcome
    | InventoryInconsistentOutcome
    | ConfirmationSummaryOutcome
    | ConfirmationCancelledOutcome
    | ConfirmationAlreadyProcessedOutcome
    | ConfirmationNothingPendingOutcome
    | ConfirmationRecoveringOutcome
    | SessionConflictOutcome
    | ExecutionSubmittedOutcome
    | ExecutionCompletedOutcome
    | ExecutionRetryableFailureOutcome
    | ExecutionPermanentFailureOutcome
    | PermissionDeniedOutcome
    | NoCaseOutcome
    | CaseClosedOutcome
    | CaseStaleOutcome
    | ServiceListOutcome
    | UnrecognizedRequestOutcome
    | ServiceUnavailableOutcome
    | QueryEmptyOutcome
    | QueryResultOutcome
    | QueryInvalidFiltersOutcome
    | CompletionNoticeOutcome
    | SemanticUnavailableOutcome
)
