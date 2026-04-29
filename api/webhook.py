from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session as DBSession

from database import SessionLocal
from core import webhook_receiver, access_control, session_manager, workflow_engine
from clients.wechat_client import send_message
from ai.chain import AIProviderChain
from ai.claude_provider import ClaudeProvider
from ai.openai_provider import OpenAIProvider

router = APIRouter()

# single shared AI chain — created once at startup
# OpenAI is primary for now; Claude is fallback when tokens are available again
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
        print(f"[webhook] GET verification failed: {e}")
        raise HTTPException(status_code=403, detail="Verification failed")


# ── POST /webhook — incoming WeChat message ───────────────────────────────────

@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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
        print(f"[webhook] POST decryption failed: {e}")
        raise HTTPException(status_code=403, detail="Decryption failed")

    # process message asynchronously — WeChat requires response within 5 seconds
    background_tasks.add_task(_process_message, message, nonce, timestamp)

    # Smart Robot expects encrypted JSON response, not plain "success"
    ack = webhook_receiver.make_encrypted_reply("SYS_ACK_TEST_001", nonce, timestamp)
    if ack:
        return Response(content=ack, media_type="text/plain")
    return PlainTextResponse(content="success")


# ── Background pipeline ───────────────────────────────────────────────────────

def _process_message(message: dict, nonce: str, timestamp: str) -> None:
    """
    Full pipeline for one incoming message.
    Runs asynchronously after the initial response is sent to WeChat.
    """
    # TEMP DEBUG — log incoming message identifiers for admin setup
    print(f"[DEBUG] msg_type={message.get('msg_type')} chat_type={message.get('chat_type')}")
    print(f"[DEBUG] from_user={message.get('from_user')} group_id={message.get('group_id')}")

    # only handle text messages from group chats in v1
    if message.get("msg_type") != "text":
        return
    if message.get("chat_type") != "group" or not message.get("group_id"):
        return  # direct messages not supported — group chat required

    db: DBSession = SessionLocal()
    try:
        # 1. access control
        result = access_control.check_access(
            db,
            wechat_openid=message["from_user"],
            wechat_group_id=message["group_id"]
        )

        if isinstance(result, access_control.AccessDenied):
            if result.notify_user:
                send_message(message["from_user"], result.message)
            return

        # 2. session routing
        session = session_manager.resolve_session(db, result, message["content"])

        # 3. build context
        context = session_manager.build_context(result, session, message)

        # 4. AI processing
        ai_response = ai_chain.process(context)

        # 5. workflow engine
        workflow_engine.run(context, ai_response, db)

    except Exception as e:
        print(f"[webhook] pipeline error: {e}")
        try:
            send_message(message["from_user"], "系统出现错误，请稍后重试或联系管理员。")
        except Exception:
            pass
    finally:
        db.close()
