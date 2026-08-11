"""Durable Kefu sync ingestion and per-staff leased message claiming."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clients.kefu_client import KefuClient, SyncPage
from core.kefu_contracts import CaseTurnProcessor, KefuIdentity, KefuInboundTurn
from models.kefu import KefuInboundMessage, KefuSyncCursor


CLAIM_SQL = text(
    """
    UPDATE kefu_inbound_message
    SET status = 'claimed', claimed_by = :worker, claimed_at = now(),
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        attempt_count = attempt_count + 1, last_error = NULL
    WHERE msgid = (
      SELECT candidate.msgid
      FROM kefu_inbound_message AS candidate
      WHERE candidate.open_kfid = :open_kfid
        AND candidate.external_userid = :external_userid
        AND (
          candidate.status = 'pending'
          OR (candidate.status = 'claimed' AND candidate.lease_expires_at < now())
        )
        AND NOT EXISTS (
          SELECT 1 FROM kefu_inbound_message AS outstanding
          WHERE outstanding.open_kfid = :open_kfid
            AND outstanding.external_userid = :external_userid
            AND outstanding.status = 'claimed'
            AND outstanding.lease_expires_at >= now()
            AND outstanding.msgid <> candidate.msgid
        )
      ORDER BY candidate.received_at, candidate.msgid
      LIMIT 1
      FOR UPDATE SKIP LOCKED
    )
    RETURNING msgid, open_kfid, external_userid, payload, received_at
    """
)

CASE_NUMBER_PATTERN = re.compile(r"(?<![A-Z0-9])CASE-\d{8}-\d{6}(?![A-Z0-9])", re.IGNORECASE)


def extract_case_number_hint(message_content: str | None) -> str | None:
    """Return only an explicitly pasted case number, never inferred case state."""
    if not message_content:
        return None
    match = CASE_NUMBER_PATTERN.search(message_content)
    return match.group(0).upper() if match else None


def normalize_message(message: dict) -> dict:
    required = ("msgid", "open_kfid", "external_userid", "msgtype")
    missing = [name for name in required if not message.get(name)]
    if missing:
        raise ValueError(f"Kefu message missing fields: {', '.join(missing)}")
    normalized = dict(message)
    normalized["msgid"] = str(message["msgid"])
    normalized["open_kfid"] = str(message["open_kfid"])
    normalized["external_userid"] = str(message["external_userid"])
    normalized["msgtype"] = str(message["msgtype"])
    return normalized


def ingest_sync_page(db: Session, *, open_kfid: str, page: SyncPage) -> int:
    """Insert one page and advance its cursor in the caller's transaction."""
    inserted = 0
    for raw in page.messages:
        message = normalize_message(raw)
        statement = (
            insert(KefuInboundMessage)
            .values(
                msgid=message["msgid"],
                open_kfid=message["open_kfid"],
                external_userid=message["external_userid"],
                payload=message,
                received_at=datetime.fromtimestamp(
                    int(message.get("send_time") or datetime.now(timezone.utc).timestamp()),
                    tz=timezone.utc,
                ),
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=[KefuInboundMessage.msgid])
            .returning(KefuInboundMessage.msgid)
        )
        if db.execute(statement).scalar_one_or_none() is not None:
            inserted += 1

    cursor_statement = (
        insert(KefuSyncCursor)
        .values(open_kfid=open_kfid, cursor=page.next_cursor, updated_at=datetime.now(timezone.utc))
        .on_conflict_do_update(
            index_elements=[KefuSyncCursor.open_kfid],
            set_={"cursor": page.next_cursor, "updated_at": datetime.now(timezone.utc)},
        )
    )
    db.execute(cursor_statement)
    return inserted


def sync_available_messages(
    db_factory: Callable[[], Session],
    client: KefuClient,
    *,
    sync_token: str,
    open_kfid: str,
) -> int:
    total = 0
    while True:
        db = db_factory()
        try:
            with db.begin():
                # Transaction-scoped lock makes cursor read -> API page -> cursor
                # advance a true per-account single-writer operation, including
                # across multiple application processes.
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"kefu-sync\x1f{open_kfid}"},
                )
                cursor = db.get(KefuSyncCursor, open_kfid)
                cursor_value = cursor.cursor if cursor else ""
                page = client.sync_messages(
                    sync_token=sync_token,
                    cursor=cursor_value,
                    open_kfid=open_kfid,
                )
                if page.has_more and page.next_cursor == cursor_value:
                    raise RuntimeError("Kefu sync pagination did not advance")
                total += ingest_sync_page(db, open_kfid=open_kfid, page=page)
        finally:
            db.close()
        if not page.has_more:
            return total


def ready_identities(db: Session, limit: int = 100) -> list[KefuIdentity]:
    rows = db.execute(
        select(KefuInboundMessage.open_kfid, KefuInboundMessage.external_userid)
        .where(
            (KefuInboundMessage.status == "pending")
            | (
                (KefuInboundMessage.status == "claimed")
                & (KefuInboundMessage.lease_expires_at < datetime.now(timezone.utc))
            )
        )
        .distinct()
        .limit(limit)
    ).all()
    return [KefuIdentity(open_kfid=row[0], external_userid=row[1]) for row in rows]


def claim_next(
    db: Session,
    *,
    identity: KefuIdentity,
    worker_id: str,
    lease_seconds: int,
) -> KefuInboundTurn | None:
    lock_key = f"{identity.open_kfid}\x1f{identity.external_userid}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key})
    row = db.execute(
        CLAIM_SQL,
        {
            "open_kfid": identity.open_kfid,
            "external_userid": identity.external_userid,
            "worker": worker_id,
            "lease_seconds": int(lease_seconds),
        },
    ).mappings().one_or_none()
    db.commit()
    if row is None:
        return None
    payload = dict(row["payload"])
    content = None
    if payload.get("msgtype") == "text":
        content = (payload.get("text") or {}).get("content")
    return KefuInboundTurn(
        identity=identity,
        msgid=row["msgid"],
        received_at=row["received_at"],
        msgtype=str(payload.get("msgtype") or "unknown"),
        content=content,
        payload=payload,
        case_number_hint=extract_case_number_hint(content),
    )


def mark_processed(db: Session, msgid: str) -> None:
    db.execute(
        text(
            "UPDATE kefu_inbound_message SET status='processed', processed_at=now(), "
            "lease_expires_at=NULL, last_error=NULL WHERE msgid=:msgid"
        ),
        {"msgid": msgid},
    )
    db.commit()


def mark_failed(db: Session, msgid: str, error: str) -> None:
    db.execute(
        text(
            "UPDATE kefu_inbound_message SET status='failed', processed_at=now(), "
            "lease_expires_at=NULL, last_error=:error WHERE msgid=:msgid"
        ),
        {"msgid": msgid, "error": error[:4000]},
    )
    db.commit()


def renew_claim(db: Session, *, msgid: str, worker_id: str, lease_seconds: int) -> bool:
    result = db.execute(
        text(
            "UPDATE kefu_inbound_message "
            "SET lease_expires_at=now() + make_interval(secs => :lease_seconds) "
            "WHERE msgid=:msgid AND status='claimed' AND claimed_by=:worker"
        ),
        {"msgid": msgid, "worker": worker_id, "lease_seconds": int(lease_seconds)},
    )
    db.commit()
    return result.rowcount == 1


@contextmanager
def lease_heartbeat(
    db_factory: Callable[[], Session],
    *,
    msgid: str,
    worker_id: str,
    lease_seconds: int,
):
    """Extend a live claim while the case service performs a long operation."""
    stop = threading.Event()
    interval = max(1.0, lease_seconds / 3)

    def heartbeat() -> None:
        while not stop.wait(interval):
            heartbeat_db = None
            try:
                heartbeat_db = db_factory()
                if not renew_claim(
                    heartbeat_db,
                    msgid=msgid,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                ):
                    return
            except Exception:
                # Business idempotency remains authoritative if renewal fails;
                # the lease will eventually be reclaimable by another worker.
                return
            finally:
                if heartbeat_db is not None:
                    heartbeat_db.close()

    thread = threading.Thread(target=heartbeat, name=f"kefu-lease-{msgid}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=min(5.0, interval + 0.5))


def run_worker_once(
    db_factory: Callable[[], Session],
    processor: CaseTurnProcessor,
    *,
    worker_id: str,
    lease_seconds: int = 300,
) -> int:
    list_db = db_factory()
    try:
        identities = ready_identities(list_db)
    finally:
        list_db.close()
    processed = 0
    for identity in identities:
        claim_db = db_factory()
        try:
            turn = claim_next(
                claim_db,
                identity=identity,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        finally:
            claim_db.close()
        if turn is None:
            continue
        try:
            with lease_heartbeat(
                db_factory,
                msgid=turn.msgid,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            ):
                processor(
                    identity=turn.identity,
                    message_content=turn.content or "",
                    message_meta={"msgid": turn.msgid, "received_at": turn.received_at},
                    case_number_hint=turn.case_number_hint,
                )
        except Exception as exc:
            result_db = db_factory()
            try:
                mark_failed(result_db, turn.msgid, str(exc))
            finally:
                result_db.close()
            continue
        result_db = db_factory()
        try:
            mark_processed(result_db, turn.msgid)
        finally:
            result_db.close()
        processed += 1
    return processed
