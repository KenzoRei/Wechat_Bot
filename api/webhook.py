import threading
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session as DBSession

from database import SessionLocal
from core import webhook_receiver, access_control, session_manager, workflow_engine
from clients.wechat_client import send_message
from ai.chain import AIProviderChain
from ai.claude_provider import ClaudeProvider
from ai.openai_provider import OpenAIProvider

router = APIRouter()

ai_chain = AIProviderChain(providers=[
    OpenAIProvider(),
    ClaudeProvider(),
])

# ── Deduplication ─────────────────────────────────────────────────────────────
import time as _time
_seen_msg_ids: dict[str, float] = {}
_DEDUP_TTL = 60

def _is_duplicate(msg_id: str) -> bool:
    now = _time.time()
    expired = [k for k, v in list(_seen_msg_ids.items()) if now - v > _DEDUP_TTL]
    for k in expired:
        del _seen_msg_ids[k]
    if msg_id in _seen_msg_ids:
        return True
    _seen_msg_ids[msg_id] = now
    return False


# ── GET /webhook — WeChat URL verification ────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str
):
    try:
        plain = webhook_receiver.handle_get_webhook(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(content=plain)
    except ValueError as e:
        print(f"[webhook] GET verification failed: {e}", flush=True)
        raise HTTPException(status_code=403, detail="Verification failed")


# ── POST /webhook — incoming WeChat message ───────────────────────────────────

@router.post("/webhook")
async def receive_webhook(
    request: Request,
    msg_signature: str,
    timestamp: str,
    nonce: str,
):
    body = await request.body()

    try:
        message = webhook_receiver.handle_post_webhook(
            body=body,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
    except ValueError as e:
        print(f"[webhook] POST decryption failed: {e}", flush=True)
        raise HTTPException(status_code=403, detail="Decryption failed")

    print(f"[webhook] from={message.get('from_user')} group={message.get('group_id')} type={message.get('msg_type')}", flush=True)

    if _is_duplicate(message.get("msg_id", "")):
        print(f"[pipeline] duplicate msg_id dropped: {message.get('msg_id')}", flush=True)
        # return empty ack — WeChat still gets 200, but no content shown
        return PlainTextResponse(content="success")

    # Start background thread for AI processing — returns immediately to WeChat
    # Thread sends actual reply via message["response_url"]
    thread = threading.Thread(
        target=_process_message,
        args=(message,),
        daemon=True
    )
    thread.start()

    # Return immediate ack so WeChat doesn't time out waiting
    ack = webhook_receiver.make_encrypted_reply("收到，处理中...", nonce, timestamp)
    if ack:
        return Response(content=ack, media_type="text/plain")
    return PlainTextResponse(content="success")


# ── Background pipeline ───────────────────────────────────────────────────────

def _process_message(message: dict) -> None:
    """
    Processes message in a background thread.
    Sends actual AI reply via message["response_url"].
    """
    if message.get("msg_type") != "text":
        return
    if message.get("chat_type") != "group" or not message.get("group_id"):
        return

    response_url = message.get("response_url", "")

    db: DBSession = SessionLocal()
    try:
        print(f"[pipeline] response_url present: {bool(response_url)}", flush=True)
        print("[pipeline] access control...", flush=True)
        result = access_control.check_access(
            db,
            wechat_openid=message["from_user"],
            wechat_group_id=message["group_id"]
        )
        print(f"[pipeline] access: {type(result).__name__}", flush=True)

        if isinstance(result, access_control.AccessDenied):
            if result.notify_user:
                send_message(message["from_user"], result.message, response_url=response_url)
            return

        session = session_manager.resolve_session(db, result, message["content"])
        context = session_manager.build_context(result, session, message)

        print("[pipeline] calling AI...", flush=True)
        ai_response = ai_chain.process(context)
        print(f"[pipeline] intent={ai_response.intent} service={ai_response.service_type_name} reply={ai_response.reply[:40]}", flush=True)

        reply = workflow_engine.run_and_get_reply(context, ai_response, db)
        print(f"[pipeline] reply={str(reply)[:40]}", flush=True)

        # Send actual reply via response_url
        if reply:
            send_message(message["from_user"], reply, response_url=response_url)

    except Exception as e:
        print(f"[pipeline] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            send_message(message["from_user"], "系统出现错误，请稍后重试或联系管理员。", response_url=response_url)
        except Exception:
            pass
    finally:
        db.close()
