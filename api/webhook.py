from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse
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
        response = webhook_receiver.handle_get_webhook(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(content=response)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid signature")


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
            xml_body=body.decode("utf-8"),
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid signature")

    # return 200 immediately — WeChat requires response within 5 seconds
    background_tasks.add_task(_process_message, message)
    return PlainTextResponse(content="success")


# ── Background pipeline ───────────────────────────────────────────────────────

def _process_message(message: dict) -> None:
    """
    Full pipeline for one incoming message.
    Runs asynchronously after the 200 response is sent to WeChat.
    """
    # only handle text messages from group chats in v1
    if message.get("msg_type") != "text":
        return
    if message.get("chat_type") != "group" or not message.get("group_id"):
        return  # direct messages not supported — group @mention required

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
        # last-resort catch — log and try to notify user
        print(f"[webhook] pipeline error: {e}")
        try:
            send_message(message["from_user"], "系统出现错误，请稍后重试或联系管理员。")
        except Exception:
            pass
    finally:
        db.close()
