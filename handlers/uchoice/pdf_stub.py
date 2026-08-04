from handlers.base import BaseHandler


class GeneratePdfStubHandler(BaseHandler):
    """
    Stubbed per explicit user decision — PDF generation library not chosen
    yet (docs/uchoice-design.md backlog). Placeholder so the rest of the
    completion workflows (storage math, request_log update, cross-group
    push) is fully functional; swap in real generation later without
    touching any other step.
    """

    def handle(self, context: dict, config: dict, db=None) -> dict:
        doc_type = config.get("doc_type", "confirmation")
        print(f"[uchoice] PDF generation stubbed (doc_type={doc_type}) — not yet implemented", flush=True)
        return {"pdf_url": None, "pdf_status": "pending"}
