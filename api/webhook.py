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

    # Process synchronously — Render free tier kills background tasks
    # GPT-4o conversation steps complete in ~1-3s, within WeChat's 5s limit
    reply_content = _process_message(message)

    ack = webhook_receiver.make_encrypted_reply(reply_content, nonce, timestamp)
    if ack:
        return Response(content=ack, media_type="text/plain")
    return PlainTextResponse(content="success")


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _process_message(message: dict) -> str:
    """
    Processes one incoming message synchronously.
    Returns the reply content string to send back to WeChat.
    """
    if message.get("msg_type") != "text":
        return ""
    if message.get("chat_type") != "group" or not message.get("group_id"):
        return ""

    db: DBSession = SessionLocal()
    try:
        print("[pipeline] access control...", flush=True)
        result = access_control.check_access(
            db,
            wechat_openid=message["from_user"],
            wechat_group_id=message["group_id"]
        )
        print(f"[pipeline] access: {type(result).__name__}", flush=True)

        if isinstance(result, access_control.AccessDenied):
            return result.message if result.notify_user else ""

        session = session_manager.resolve_session(db, result, message["content"])
        context = session_manager.build_context(result, session, message)

        print("[pipeline] calling AI...", flush=True)
        ai_response = ai_chain.process(context)
        print(f"[pipeline] intent={ai_response.intent} reply={ai_response.reply[:40]}", flush=True)

        # run workflow engine — it returns the reply via context
        # for conversation steps, reply is in ai_response
        # for workflow execution, reply_wechat handler sends via response_url
        reply = workflow_engine.run_and_get_reply(context, ai_response, db)
        print(f"[pipeline] reply={str(reply)[:40]}", flush=True)
        return reply or ""

    except Exception as e:
        print(f"[pipeline] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return "系统出现错误，请稍后重试或联系管理员。"
    finally:
        db.close()
