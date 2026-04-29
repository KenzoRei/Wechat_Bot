from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AIResponse:
    intent:               str        # "new_request" | "continuation" | "confirm" |
                                     # "cancel" | "check_services" | "unrecognized"
    reply:                str        # Chinese message to send to the user
    extracted_fields:     dict       # fields pulled from this message turn only
    all_fields_collected: bool       # True → ready to show confirmation template
    service_type_name:    str | None # set when intent == "new_request"


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
