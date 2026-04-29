from handlers.label.base import YDDLabelBaseHandler


class UPSLabelHandler(YDDLabelBaseHandler):
    """
    Creates a UPS shipping label via YiDiDa.
    All logic is in YDDLabelBaseHandler — carrier is set via step.config.
    """
