from abc import ABC, abstractmethod
from sqlalchemy.orm import Session as DBSession


class BaseHandler(ABC):

    @abstractmethod
    def handle(self, context: dict, config: dict, db: DBSession) -> dict:
        """
        Executes one workflow step.

        Args:
            context: full pipeline context dict (collected_fields, result, etc.)
            config:  merged dict of step-level config + group-level config
            db:      the active DB session — same one workflow_engine is using,
                     so handler writes participate in the same transaction

        Returns:
            dict of results to merge into context["result"].
            Return {} if this step produces no output (e.g. reply_wechat).

        Raises:
            RuntimeError on failure — workflow_engine catches this and marks
            the session and request_log as failed.
        """
