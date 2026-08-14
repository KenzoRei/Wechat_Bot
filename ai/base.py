from abc import ABC, abstractmethod
from dataclasses import dataclass


# Stable code plus the field it
# concerns and the evidence behind it, for AI-reported issues the backend
# cannot resolve on its own (an ambiguous/unknown/contradictory value against
# a supplied catalog or candidate set). Never used to compose reply text
# directly -- core/kefu_response_renderer.py renders from backend-validated
# facts, this is only the AI's structured evidence for the backend to judge.
@dataclass(frozen=True)
class SemanticIssue:
    code: str                         # "ambiguous_value" | "unknown_value" | "contradictory_value"
    field: str                        # the extracted_fields key this concerns
    candidate_ids: tuple[str, ...] = ()  # only IDs drawn from the turn's own supplied candidate set
    value: str | None = None          # the raw value that couldn't be resolved, when relevant


# Semantic evidence for matching a
# free-text destination description against the shared U-Choice address
# candidates supplied for this turn. The backend (core/kefu_response_renderer
# .validate_address_match) is the only thing that decides whether a match is
# accepted -- this is evidence, not a decision.
@dataclass(frozen=True)
class AddressMatch:
    status: str                       # "matched" | "ambiguous" | "unmatched" | "not_provided"
    candidate_ids: tuple[str, ...] = ()  # matched: exactly one; ambiguous: two or more
    new_address: dict | None = None   # unmatched only: best-effort sanitized {"company_name": ..., "addr": ...}


@dataclass
class AIResponse:
    intent:               str        # "new_request" | "continuation" | "confirm" |
                                     # "cancel" | "check_services" | "unrecognized"
    reply:                str        # Chinese message to send to the user.
                                     # Legacy Smart Robot field. Kefu must never
                                     # deliver this as an operational response --
                                     # every Kefu-owned orchestration file renders
                                     # from core/kefu_response_renderer.py instead.
    extracted_fields:     dict       # fields pulled from this message turn only
    all_fields_collected: bool       # Hint only, never trusted as a bypass. The
                                     # backend independently computes readiness
                                     # from schema/validators for every channel.
    service_type_name:    str | None # set when intent == "new_request"
    # uchoice_outbound_request only, Smart Robot legacy path: set when the
    # customer described a delivery destination that matched nothing in the
    # injected address candidate list — a best-effort {"company_name": ...,
    # "addr": ...} guess (either key may be absent) for seeding a fresh
    # upsert_address session, since this is a genuinely different situation
    # from "the customer hasn't mentioned a destination yet" (which is just a
    # normal missing-field wait, not this signal). Kefu consumes
    # `address_match.new_address` instead during the transition period and
    # afterward; this field is parsed only for Smart Robot.
    unmatched_new_address: dict | None = None
    # Structured semantic evidence populated for any provider response,
    # regardless of channel -- Kefu orchestration is expected to consume
    # these; Smart Robot's legacy path ignores them.
    semantic_issues: tuple[SemanticIssue, ...] = ()
    address_match: AddressMatch | None = None


class AIProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name used in logs and error messages."""

    @abstractmethod
    def process(self, context: dict) -> AIResponse:
        """
        Takes the full pipeline context dict.
        Returns a structured AIResponse.
        Raises an exception on failure — AIProviderChain handles fallback.
        """
