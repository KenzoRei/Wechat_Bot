"""Regenerate durable Kefu file payloads from stable database references."""
from __future__ import annotations

from uuid import UUID

from core.kefu_contracts import Artifact, ArtifactLike


def load_artifact(request_log_id: UUID, doc_type: str, artifact_key: str) -> ArtifactLike:
    """Rebuild an artifact identically after deferral or process restart."""
    from database import SessionLocal
    from handlers.uchoice.pdf_stub import GeneratePdfStubHandler
    from models.request_log import RequestLog
    from models.session import ConversationSession

    db = SessionLocal()
    try:
        log = db.get(RequestLog, request_log_id)
        if log is None:
            raise LookupError(f"request_log {request_log_id} not found")
        session = (
            db.query(ConversationSession)
            .filter(
                (ConversationSession.request_log_id == request_log_id)
                | (ConversationSession.session_id == log.origin_session_id)
            )
            .order_by(ConversationSession.created_at.asc())
            .first()
        )
        if session is None:
            raise LookupError(f"source session for request_log {request_log_id} not found")
        context = {
            "request_log_id": str(log.log_id),
            "serial_number": log.serial_number,
            "collected_fields": session.collected_fields or {},
        }
        if doc_type != "outbound_instruction":
            raise ValueError(f"unsupported durable artifact doc_type: {doc_type}")
        mapping = GeneratePdfStubHandler._build_outbound_instruction_artifact(context, db)
        if mapping is None:
            raise RuntimeError(f"failed to regenerate {doc_type} for {request_log_id}")
        artifact = Artifact.from_mapping(mapping)
        if artifact.artifact_key != artifact_key:
            raise ValueError("artifact_key_mismatch")
        return artifact
    finally:
        db.close()
