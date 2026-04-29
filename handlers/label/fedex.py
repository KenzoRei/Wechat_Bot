from handlers.label.base import YDDLabelBaseHandler


class FedExLabelHandler(YDDLabelBaseHandler):
    """
    Creates a FedEx shipping label via YiDiDa.
    All logic is in YDDLabelBaseHandler — carrier is set via step.config.
    """
