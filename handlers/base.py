from abc import ABC, abstractmethod


class BaseHandler(ABC):

    @abstractmethod
    def handle(self, context: dict, config: dict) -> dict:
        """
        Executes one workflow step.

        Args:
            context: full pipeline context dict (collected_fields, result, etc.)
            config:  merged dict of step-level config + group-level config

        Returns:
            dict of results to merge into context["result"].
            Return {} if this step produces no output (e.g. reply_wechat).

        Raises:
            RuntimeError on failure — workflow_engine catches this and marks
            the session and request_log as failed.
        """
