"""Durable, at-least-once Kefu text/file delivery for staff-facing replies."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clients.kefu_client import (
    KefuAPIError,
    KefuClient,
    KefuQuotaExceeded,
    KefuTransportError,
    KefuWindowClosed,
)
from core.kefu_contracts import Artifact, ArtifactLike, KefuIdentity, coerce_artifact
from models.kefu import KefuOutboundDelivery, KefuStaff


@dataclass(frozen=True)
class TextPayload:
    text: str


@dataclass(frozen=True)
class FilePayload:
    artifact: Artifact
    expected_hash: str


@dataclass(frozen=True)
class Sent:
    provider_message_id: str


@dataclass(frozen=True)
class WindowClosed:
    pass


@dataclass(frozen=True)
class QuotaExceeded:
    pass


@dataclass(frozen=True)
class Retryable:
    error: str


@dataclass(frozen=True)
class Failed:
    error: str


SendResult = Sent | WindowClosed | QuotaExceeded | Retryable | Failed
ArtifactLoader = Callable[[uuid.UUID, str, str], ArtifactLike]


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def send_reply(
    client: KefuClient,
    *,
    recipient: KefuIdentity,
    delivery_key: str,
    payload: TextPayload | FilePayload,
) -> SendResult:
    """Attempt one provider send. Persistence and retry policy live below."""
    try:
        if isinstance(payload, TextPayload):
            provider_id = client.send_text(
                open_kfid=recipient.open_kfid,
                external_userid=recipient.external_userid,
                text=payload.text,
                msgid=delivery_key,
            )
        else:
            actual_hash = content_hash(payload.artifact.content)
            if actual_hash != payload.expected_hash:
                return Failed("artifact_hash_mismatch")
            media_id = client.upload_file(
                filename=payload.artifact.filename,
                content=payload.artifact.content,
                content_type=payload.artifact.content_type,
            )
            provider_id = client.send_file(
                open_kfid=recipient.open_kfid,
                external_userid=recipient.external_userid,
                media_id=media_id,
                msgid=delivery_key,
            )
        return Sent(provider_id)
    except KefuWindowClosed:
        return WindowClosed()
    except KefuQuotaExceeded:
        return QuotaExceeded()
    except (KefuTransportError, requests.RequestException) as exc:
        return Retryable(str(exc) or type(exc).__name__)
    except KefuAPIError as exc:
        return Failed(str(exc))
    except (RuntimeError, ValueError) as exc:
        return Failed(str(exc))


def enqueue_text(
    db: Session,
    *,
    recipient_staff_id: uuid.UUID,
    idempotency_key: str,
    text_content: str,
    session_id: uuid.UUID | None = None,
    request_log_id: uuid.UUID | None = None,
) -> KefuOutboundDelivery:
    if (session_id is None) == (request_log_id is None):
        raise ValueError("exactly one delivery target is required")
    statement = (
        insert(KefuOutboundDelivery)
        .values(
            session_id=session_id,
            request_log_id=request_log_id,
            recipient_staff_id=recipient_staff_id,
            idempotency_key=idempotency_key,
            payload_type="text",
            text_content=text_content,
            payload_hash=content_hash(text_content.encode("utf-8")),
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=[KefuOutboundDelivery.idempotency_key])
    )
    db.execute(statement)
    delivery = db.execute(
        select(KefuOutboundDelivery).where(KefuOutboundDelivery.idempotency_key == idempotency_key)
    ).scalar_one()
    expected_hash = content_hash(text_content.encode("utf-8"))
    if not (
        delivery.payload_type == "text"
        and delivery.recipient_staff_id == recipient_staff_id
        and delivery.session_id == session_id
        and delivery.request_log_id == request_log_id
        and delivery.payload_hash == expected_hash
    ):
        raise ValueError("idempotency_key_collision")
    return delivery


def enqueue_file(
    db: Session,
    *,
    recipient_staff_id: uuid.UUID,
    idempotency_key: str,
    request_log_id: uuid.UUID,
    doc_type: str,
    artifact: ArtifactLike,
) -> KefuOutboundDelivery:
    artifact = coerce_artifact(artifact)
    statement = (
        insert(KefuOutboundDelivery)
        .values(
            request_log_id=request_log_id,
            recipient_staff_id=recipient_staff_id,
            idempotency_key=idempotency_key,
            payload_type="file",
            artifact_request_log_id=request_log_id,
            artifact_doc_type=doc_type,
            artifact_key=artifact.artifact_key,
            payload_hash=content_hash(artifact.content),
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=[KefuOutboundDelivery.idempotency_key])
    )
    db.execute(statement)
    delivery = db.execute(
        select(KefuOutboundDelivery).where(KefuOutboundDelivery.idempotency_key == idempotency_key)
    ).scalar_one()
    expected_hash = content_hash(artifact.content)
    if not (
        delivery.payload_type == "file"
        and delivery.recipient_staff_id == recipient_staff_id
        and delivery.request_log_id == request_log_id
        and delivery.artifact_request_log_id == request_log_id
        and delivery.artifact_doc_type == doc_type
        and delivery.artifact_key == artifact.artifact_key
        and delivery.payload_hash == expected_hash
    ):
        raise ValueError("idempotency_key_collision")
    return delivery


def _payload_for(delivery: KefuOutboundDelivery, artifact_loader: ArtifactLoader) -> TextPayload | FilePayload:
    if delivery.payload_type == "text":
        return TextPayload(delivery.text_content or "")
    if not (
        delivery.artifact_request_log_id
        and delivery.artifact_doc_type
        and delivery.artifact_key
    ):
        raise ValueError("incomplete durable artifact reference")
    artifact = coerce_artifact(artifact_loader(
        delivery.artifact_request_log_id,
        delivery.artifact_doc_type,
        delivery.artifact_key,
    ))
    if artifact.artifact_key != delivery.artifact_key:
        raise ValueError("artifact_key_mismatch")
    return FilePayload(artifact=artifact, expected_hash=delivery.payload_hash)


def deliver_one(
    db: Session,
    client: KefuClient,
    *,
    delivery_id: uuid.UUID,
    artifact_loader: ArtifactLoader,
    retry_delay: timedelta = timedelta(minutes=15),
) -> SendResult | None:
    """Serialize and attempt one pending row, recording the result atomically.

    The database transaction intentionally remains open across the provider call:
    the advisory lock prevents two workers sending the same idempotency key at
    once. A lost provider response still creates a narrow duplicate risk on
    retry, so delivery is at-least-once rather than exactly-once.
    """
    delivery = db.execute(
        select(KefuOutboundDelivery)
        .where(KefuOutboundDelivery.delivery_id == delivery_id)
        .with_for_update()
    ).scalar_one_or_none()
    if delivery is None or delivery.status != "pending":
        db.rollback()
        return None
    now = datetime.now(timezone.utc)
    if delivery.next_retry_at and delivery.next_retry_at > now:
        db.rollback()
        return None
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": delivery.idempotency_key},
    )
    staff = db.get(KefuStaff, delivery.recipient_staff_id)
    if staff is None or not staff.is_active:
        result: SendResult = Failed("recipient_staff_unavailable")
    else:
        try:
            payload = _payload_for(delivery, artifact_loader)
        except (RuntimeError, ValueError) as exc:
            result = Failed(str(exc))
        else:
            result = send_reply(
                client,
                recipient=KefuIdentity(staff.open_kfid, staff.external_userid),
                delivery_key=delivery.idempotency_key,
                payload=payload,
            )

    delivery.attempt_count += 1
    delivery.updated_at = now
    if isinstance(result, Sent):
        delivery.status = "sent"
        delivery.provider_message_id = result.provider_message_id
        delivery.sent_at = now
        delivery.next_retry_at = None
        delivery.last_error = None
    elif isinstance(result, WindowClosed):
        delivery.last_error = "window_closed"
        delivery.next_retry_at = None
    elif isinstance(result, QuotaExceeded):
        delivery.last_error = "quota_exceeded"
        delivery.next_retry_at = now + retry_delay
    elif isinstance(result, Retryable):
        delivery.last_error = result.error[:4000]
        delivery.next_retry_at = now + retry_delay
    else:
        delivery.status = "failed"
        delivery.last_error = result.error[:4000]
        delivery.next_retry_at = None
    db.commit()
    return result


def pending_for_staff(db: Session, staff_id: uuid.UUID, *, limit: int = 1) -> list[uuid.UUID]:
    """Return oldest deferred work; MVP sends one item per reopened window."""
    return list(
        db.execute(
            select(KefuOutboundDelivery.delivery_id)
            .where(
                KefuOutboundDelivery.recipient_staff_id == staff_id,
                KefuOutboundDelivery.status == "pending",
            )
            .order_by(KefuOutboundDelivery.created_at, KefuOutboundDelivery.delivery_id)
            .limit(limit)
        ).scalars()
    )
