"""Channel-neutral artifact wrapper for a shipping label PDF (Kefu only)."""
import base64


def build_label_artifact(label_pdf: bytes, carrier: str, serial_number: str, request_log_id) -> dict:
    """
    {bytes, filename, content_type, artifact_key} shape, matching
    core/uchoice_invoice_export.py's build_invoice_artifact -- so Kefu
    delivery (core/kefu_delivery.py's enqueue_file) can handle a label
    exactly like any other durable Kefu file.

    Unlike an invoice workbook or PDF stub, a label is NOT safely
    regenerable from scratch: label_pdf must be bytes ALREADY obtained
    from a real, one-time YiDiDa API call (create_label), which creates an
    actual carrier shipment -- calling it again would create a SECOND,
    duplicate, separately-billed shipment. artifact_key is stable per
    request_log_id (there is only ever one label per label request);
    core/kefu_artifact_loader.py's "label" doc_type must read the
    already-created label back from label_shipment.label_pdf, never call
    create_label again.
    """
    return {
        "bytes": label_pdf,
        "filename": f"{carrier}_label_{serial_number}.pdf",
        "content_type": "application/pdf",
        "artifact_key": f"{request_log_id}:label",
    }


def build_label_artifact_from_base64(label_base64: str, carrier: str, serial_number: str, request_log_id) -> dict:
    """Convenience wrapper for the initial send, where the label step's
    own result still holds label_base64 (as YiDiDa returned it) rather
    than the decoded bytes label_shipment stores."""
    return build_label_artifact(base64.b64decode(label_base64), carrier, serial_number, request_log_id)
