"""Isolated WeChat Kefu callback router; application wiring is feature-gated elsewhere."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from core import ierror
from core.WXBizXmlMsgCrypt import MAX_XML_BYTES, WXBizXmlMsgCrypt


@dataclass(frozen=True)
class KefuSyncEvent:
    sync_token: str
    open_kfid: str | None
    create_time: int | None


def parse_sync_event(plain_xml: str | bytes) -> KefuSyncEvent:
    raw = plain_xml.encode("utf-8") if isinstance(plain_xml, str) else plain_xml
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("callback XML is too large")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("malformed callback XML") from exc
    if root.tag != "xml":
        raise ValueError("unexpected callback XML root")
    allowed = {"ToUserName", "CreateTime", "Event", "Token", "OpenKfId"}
    if any(child.tag not in allowed for child in root):
        raise ValueError("unexpected callback XML field")
    fields = {child.tag: (child.text or "").strip() for child in root}
    if fields.get("Event", "").lower() != "kf_msg_or_event":
        raise ValueError("unexpected Kefu callback event")
    sync_token = fields.get("Token")
    if not sync_token:
        raise ValueError("Kefu callback omitted Token")
    create_time_text = fields.get("CreateTime")
    try:
        create_time = int(create_time_text) if create_time_text else None
    except ValueError as exc:
        raise ValueError("invalid callback CreateTime") from exc
    return KefuSyncEvent(
        sync_token=sync_token,
        open_kfid=fields.get("OpenKfId") or None,
        create_time=create_time,
    )


def create_kefu_callback_router(
    crypt: WXBizXmlMsgCrypt,
    on_sync_event: Callable[[KefuSyncEvent], None],
) -> APIRouter:
    """Build the callback surface without importing credentials or starting workers."""
    router = APIRouter()

    @router.get("/kefu/callback")
    async def verify_callback(msg_signature: str, timestamp: str, nonce: str, echostr: str):
        code, plain = crypt.VerifyURL(msg_signature, timestamp, nonce, echostr)
        if code != ierror.WXBizMsgCrypt_OK or plain is None:
            raise HTTPException(status_code=403, detail="Verification failed")
        return PlainTextResponse(plain)

    @router.post("/kefu/callback")
    async def receive_callback(
        request: Request,
        background_tasks: BackgroundTasks,
        msg_signature: str,
        timestamp: str,
        nonce: str,
    ):
        body = await request.body()
        code, plain = crypt.DecryptMsg(body, msg_signature, timestamp, nonce)
        if code != ierror.WXBizMsgCrypt_OK or plain is None:
            raise HTTPException(status_code=403, detail="Verification failed")
        try:
            event = parse_sync_event(plain)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        background_tasks.add_task(on_sync_event, event)
        return PlainTextResponse("success")

    return router
