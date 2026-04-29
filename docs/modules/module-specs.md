# Module Specifications
# Logistics WeChat Bot Platform — v1

**Version:** 1.1
**Date:** 2026-04-29
**Status:** Finalized — All 13 modules complete + Phase 6 corrections applied

---

## Overview

This document covers the detailed design of every module in the system.
For the folder layout, see `project-structure.md`.

Pipeline flow (one incoming WeChat message):

```
webhook_receiver → access_control → session_manager → AI Provider Chain
      → confirmation (if needed) → workflow_engine → request_logger
      → wechat_client (reply)
```

---

## Module 1 — config.py

**Role:** Single source of truth for all environment variables. Every other
module imports from here — no module reads `os.environ` directly.

### Design

```python
# config.py

import os

def _require(name: str) -> str:
    """Raise at startup if a required env var is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

# WeChat Work
WECHAT_CORP_ID          = _require("WECHAT_CORP_ID")
WECHAT_SECRET           = _require("WECHAT_SECRET")       # agent secret → access tokens
WECHAT_AGENT_ID         = _require("WECHAT_AGENT_ID")
WECHAT_TOKEN            = _require("WECHAT_TOKEN")         # webhook config
WECHAT_ENCODING_AES_KEY = _require("WECHAT_ENCODING_AES_KEY")

# External APIs
YIDIDA_API_KEY   = _require("YIDIDA_API_KEY")
YIDIDA_BASE_URL  = _require("YIDIDA_BASE_URL")
OMS_API_KEY      = _require("OMS_API_KEY")
OMS_BASE_URL     = _require("OMS_BASE_URL")

# Claude AI
CLAUDE_API_KEY = _require("CLAUDE_API_KEY")
CLAUDE_MODEL   = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Admin
ADMIN_API_KEY = _require("ADMIN_API_KEY")

# Database
DATABASE_URL = _require("DATABASE_URL")

# Session
SESSION_EXPIRY_MINUTES = int(os.getenv("SESSION_EXPIRY_MINUTES", "60"))
```

### Key rules
- `_require()` raises `RuntimeError` at startup, not at request time. Bad config fails fast.
- `SESSION_EXPIRY_MINUTES` has a safe default (60) — not a required var.
- `CLAUDE_MODEL` has a safe default — lets you override in .env without a code change.
- No other module calls `os.getenv()` directly.

---

## Module 2 — database.py

**Role:** Creates the SQLAlchemy engine and session factory. Provides
`get_db()` as a FastAPI dependency injected into route handlers.

### Design

```python
# database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import config

engine = create_engine(config.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    """
    FastAPI dependency. Yields one DB session per request, always closes it.
    Usage in route: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### Key rules
- `get_db()` is a generator function — FastAPI calls `next()` to get the session,
  runs the route handler, then resumes after `yield` to close it.
- All ORM models import `Base` from here and call `Base.metadata.create_all(engine)`
  is NOT used — migrations handle schema creation instead.
- `autocommit=False` — code must call `db.commit()` explicitly. No hidden commits.

---

## Module 3 — middleware/admin_auth.py

**Role:** Validates the `X-Admin-Key` header on all admin routes.
Returns HTTP 401 if the key is missing or wrong.

### Design

```python
# middleware/admin_auth.py

from fastapi import Header, HTTPException
import config

async def verify_admin_key(x_admin_key: str = Header(...)):
    """
    FastAPI dependency. Injected into every admin route via Depends().
    Raises 401 if header is missing or does not match ADMIN_API_KEY.
    """
    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
```

### Usage in route handlers

```python
from middleware.admin_auth import verify_admin_key
from fastapi import Depends

@router.get("/admin/groups", dependencies=[Depends(verify_admin_key)])
async def list_groups(db: Session = Depends(get_db)):
    ...
```

### Key rules
- This is a dependency, not a middleware class. FastAPI's `Depends()` system
  handles injection — no custom middleware stack needed.
- The header name `x_admin_key` maps to `X-Admin-Key` automatically (FastAPI
  converts snake_case parameter names from headers).
- Raises `HTTPException` directly — FastAPI converts this to a JSON 401 response.
- `Header(...)` — the `...` means the header is required. FastAPI returns 422 if
  the header is entirely absent before our check even runs.

---

## Module 4 — core/webhook_receiver.py

**Role:** Handles the two WeChat webhook endpoints.
- `GET /webhook`: signature validation for initial bot setup (URL verification)
- `POST /webhook`: signature validation + AES decryption + message extraction

**Phase 6 correction:** Uses 智能机器人 (Smart Robot) instead of 自建应用.
Critical differences from original design:
- `receiveid = ''` (empty string) — NOT Corp ID or Bot ID
- Uses official `WXBizJsonMsgCrypt` from WeChat demo, NOT wechatpy
- Messages are JSON format, not XML
- Replies are encrypted JSON stream format

### Design

```python
# core/webhook_receiver.py

from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise import WeChatClient
import xml.etree.ElementTree as ET
import hashlib
import config

crypto = WeChatCrypto(
    token=config.WECHAT_TOKEN,
    encoding_aes_key=config.WECHAT_ENCODING_AES_KEY,
    corp_id=config.WECHAT_CORP_ID
)


def validate_signature(msg_signature: str, timestamp: str, nonce: str, echo_str: str = "") -> bool:
    """
    Validates WeChat's SHA1 signature.
    GET /webhook: include echostr in the hash.
    POST /webhook: echostr is empty string.
    """
    params = sorted([config.WECHAT_TOKEN, timestamp, nonce, echo_str])
    expected = hashlib.sha1("".join(params).encode("utf-8")).hexdigest()
    return expected == msg_signature


def decrypt_message(xml_body: str, msg_signature: str, timestamp: str, nonce: str) -> str:
    """
    Decrypts the encrypted WeChat XML body.
    Returns the plaintext XML string of the inner message.
    """
    return crypto.decrypt_message(xml_body, msg_signature, timestamp, nonce)


def extract_message(decrypted_xml: str) -> dict:
    """
    Parses the decrypted XML and extracts message fields.
    Returns a structured dict for downstream processing.
    """
    root = ET.fromstring(decrypted_xml)

    return {
        "from_user":   root.findtext("FromUserName"),   # sender's WeChat openid
        "group_id":    root.findtext("ToUserName"),     # group / corp id
        "msg_type":    root.findtext("MsgType"),        # "text" for text messages
        "content":     root.findtext("Content", ""),    # message text
        "msg_id":      root.findtext("MsgId"),          # unique message id (idempotency)
        "agent_id":    root.findtext("AgentID"),
        "create_time": root.findtext("CreateTime"),
    }


def handle_get_webhook(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """
    Handles GET /webhook (WeChat URL verification).
    Returns echostr if signature is valid, raises ValueError if not.
    """
    if not validate_signature(msg_signature, timestamp, nonce, echostr):
        raise ValueError("Invalid signature")
    return echostr


def handle_post_webhook(
    xml_body: str,
    msg_signature: str,
    timestamp: str,
    nonce: str
) -> dict:
    """
    Handles POST /webhook (incoming message).
    Returns the extracted message dict for the pipeline.
    Raises ValueError if signature is invalid.
    """
    if not validate_signature(msg_signature, timestamp, nonce):
        raise ValueError("Invalid signature")

    decrypted = decrypt_message(xml_body, msg_signature, timestamp, nonce)
    return extract_message(decrypted)
```

### Key rules
- Route handler in `api/webhook.py` calls these functions and returns HTTP 200
  **immediately** — actual processing runs in `BackgroundTasks`.
- `wechatpy` handles AES-256-CBC decryption and XML unwrapping internally.
- `msg_id` is extracted here and used later by `request_logger.py` for
  idempotency (the `wechat_msg_id` unique index on `request_log`).
- Only `MsgType == "text"` messages are processed in v1. Other types (image,
  voice, etc.) are silently ignored by the pipeline.

---

## Module 5 — core/access_control.py

**Role:** Checks whether the sender is allowed to use the bot.
Loads their group membership, role, and allowed services.
Returns a structured result for the pipeline.

### Design

```python
# core/access_control.py

from dataclasses import dataclass
from uuid import UUID
from sqlalchemy.orm import Session as DBSession
from models.group import GroupConfig, GroupMember, GroupService
from models.service import ServiceType


@dataclass
class AccessResult:
    """
    Returned when a user passes all access checks.
    Passed downstream as part of the context dict.
    """
    wechat_openid:    str
    group_id:         UUID
    role:             str           # "admin" or "customer"
    display_name:     str
    allowed_services: list[dict]    # [{"service_type_id": ..., "name": ..., "workflow_id": ...}]


@dataclass
class AccessDenied:
    """
    Returned when access is denied. Contains the reason and whether to notify.
    """
    reason:         str     # internal reason code for logging
    notify_user:    bool    # True → send a reply to the user; False → silent ignore
    message:        str     # Chinese message to send (only used if notify_user=True)


def check_access(
    db: DBSession,
    wechat_openid: str,
    wechat_group_id: str     # the raw group id from WeChat XML
) -> AccessResult | AccessDenied:
    """
    Main access check. Runs in order:
    1. Group exists and is active
    2. User is a member of this group and is active
    3. Load all services allowed for this group
    """
    # Step 1: check group
    group = db.query(GroupConfig).filter_by(
        wechat_group_id=wechat_group_id,
        is_active=True
    ).first()

    if group is None:
        return AccessDenied(
            reason="group_not_found_or_inactive",
            notify_user=False,
            message=""
        )

    # Step 2: check member
    member = db.query(GroupMember).filter_by(
        wechat_openid=wechat_openid,
        group_id=group.group_id
    ).first()

    if member is None:
        return AccessDenied(
            reason="user_not_member",
            notify_user=True,
            message="抱歉，您没有权限使用此服务。"
        )

    if not member.is_active:
        return AccessDenied(
            reason="user_suspended",
            notify_user=True,
            message="您的账号已被暂停，请联系管理员。"
        )

    # Step 3: load allowed services
    services = (
        db.query(GroupService, ServiceType)
        .join(ServiceType, GroupService.service_type_id == ServiceType.service_type_id)
        .filter(
            GroupService.group_id == group.group_id,
            ServiceType.is_active == True
        )
        .all()
    )

    allowed = [
        {
            "service_type_id": str(gs.service_type_id),
            "name":            st.name,
            "workflow_id":     str(gs.workflow_id),
            "group_config":    gs.config,   # group-specific API params (ydd_cust_id, etc.)
        }
        for gs, st in services
    ]

    return AccessResult(
        wechat_openid=wechat_openid,
        group_id=group.group_id,
        role=member.role,
        display_name=member.display_name,
        allowed_services=allowed
    )
```

### Access denial rules

| Scenario | Notify user? | Reason |
|---|---|---|
| Group not found | Silent | Bot doesn't exist in this group as far as the sender knows |
| Group deactivated | Silent | Same — group-level, not user's fault |
| User not a member | Notify | They can see the bot but can't use it — they need to know |
| User suspended | Notify | They were previously a member — they need to know why it stopped working |

### Key rules
- `AccessResult` and `AccessDenied` are `dataclass` objects, not dicts — safer
  to pass around because fields are typed and named.
- `allowed_services` includes `workflow_id` per service — the Workflow Engine
  uses this to know which workflow to run per group.
- Access check is synchronous. No external API calls — DB only.
- If `AccessDenied.notify_user` is True, the pipeline sends `message` to the
  user via `wechat_client.py` and returns. No further processing.

---

## Module 6 — core/session_manager.py

**Role:** Manages the full lifecycle of `conversation_session` rows.
Assembles the `context` dict that flows through the entire pipeline.

### Session routing logic

One session at a time per user per group — enforced by the unique index on
`status IN ('active', 'pending_confirmation')`. `resolve_session()` always
returns a single session or None, never a list.

```
Incoming @Bot message
        │
        ▼
extract_serial_from_message()
        │
   ┌────┴──────────────────────────┐
serial found                  no serial
        │                          │
find_session_by_serial()    find_current_session()
        │                    (status IN active,
   ┌────┴────┐                pending_confirmation)
found    not found                 │
   │          │               ┌───┴───┐
   │    find_current_       found  not found
   │    session()             │       │
   └──────────┴───────────────┘       │
                   │                  │
         single session or None ──────┘
                   │
                   ▼
       pass to AI with full context
       AI determines intent:
  ┌──────────┬──────────┬──────────┬─────────────┐
new_request continuation confirm  cancel   unrecognized
  │            │           │         │          │
  │            │           │         │    reply: "无法理解，
  │            │           │         │     请重新描述"
  │            │           │         │
has session? update_    close_     close_
  yes → reject session() session()  session()
  "请先完成    (add msg,  (completed)(cancelled)
   当前申请"   update
  no →         fields)
create_session()
```

### Design

```python
# core/session_manager.py

import re
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session as DBSession
from models.session import ConversationSession
from models.request_log import RequestLog
from core.access_control import AccessResult
import config

SERIAL_PATTERN = re.compile(r'REQ-\d{8}-\d{6}')


def extract_serial_from_message(content: str) -> str | None:
    """
    Fast path: detect serial number in message text.
    Returns the serial string if found, None otherwise.
    """
    match = SERIAL_PATTERN.search(content)
    return match.group(0) if match else None


def find_session_by_serial(
    db: DBSession,
    serial_number: str
) -> ConversationSession | None:
    """
    Looks up an in-progress session by serial number.
    Goes via request_log → conversation_session.
    Returns None if serial not found or session is not in-progress.
    """
    log = db.query(RequestLog).filter_by(serial_number=serial_number).first()
    if log is None or log.session_id is None:
        return None
    return db.query(ConversationSession).filter(
        ConversationSession.session_id == log.session_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def find_current_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID
) -> ConversationSession | None:
    """
    Returns the one in-progress session for this user in this group, or None.
    Covers both active (collecting) and pending_confirmation (awaiting confirm).
    At most one can exist at a time — enforced by the unique index.
    """
    return db.query(ConversationSession).filter(
        ConversationSession.wechat_openid == wechat_openid,
        ConversationSession.group_id == group_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def resolve_session(
    db: DBSession,
    access: AccessResult,
    content: str
) -> ConversationSession | None:
    """
    Main routing logic. Returns the in-progress session if one exists, else None.
    Serial number is checked first (fast path); falls back to user+group lookup.
    The AI always makes the final intent decision — this just loads the context.
    """
    serial = extract_serial_from_message(content)
    if serial:
        session = find_session_by_serial(db, serial)
        if session:
            return session

    return find_current_session(db, access.wechat_openid, access.group_id)


def create_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID,
    initial_message: str,
    service_type_id: UUID | None = None
) -> ConversationSession:
    """
    Creates a new active session. Called when AI confirms a new valid request.
    Takes individual fields (not AccessResult) so workflow_engine can call it
    directly from the context dict without holding the original AccessResult.
    """
    session = ConversationSession(
        wechat_openid=wechat_openid,
        group_id=group_id,
        service_type_id=service_type_id,
        status="active",
        conversation_history=[{"role": "user", "content": initial_message}],
        collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_message(
    db: DBSession,
    session: ConversationSession,
    role: str,      # "user" or "assistant"
    content: str
) -> None:
    """
    Appends a message to conversation_history.
    Also resets expires_at — any activity restarts the 1-hour timer.
    """
    session.conversation_history = session.conversation_history + [
        {"role": role, "content": content}
    ]
    session.updated_at = datetime.now(timezone.utc)
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)
    db.commit()


def update_collected_fields(
    db: DBSession,
    session: ConversationSession,
    fields: dict
) -> None:
    """
    Merges newly extracted fields into session.collected_fields.
    Called each time Claude extracts another batch of fields from the conversation.
    """
    session.collected_fields = {**session.collected_fields, **fields}
    db.commit()


def close_session(
    db: DBSession,
    session: ConversationSession,
    status: str     # "completed" | "cancelled" | "rejected" | "failed" | "timed_out"
) -> None:
    """
    Closes a session with the given terminal status.
    Terminal status values:
      completed  → user confirmed, workflow ran successfully
      cancelled  → user explicitly cancelled ("取消")
      rejected   → user asked for a service their group doesn't have
      failed     → system/API error mid-workflow
      timed_out  → background job found expires_at passed with no activity
    """
    session.status = status
    session.updated_at = datetime.now(timezone.utc)
    db.commit()


def build_context(
    access: AccessResult,
    session: ConversationSession | None,
    message: dict
) -> dict:
    """
    Assembles the full context dict that flows through the entire pipeline.
    Called after session is resolved or created.
    All downstream modules read from this dict — nothing else is passed around.
    """
    return {
        # from access_control
        "wechat_openid":    access.wechat_openid,
        "group_id":         str(access.group_id),
        "role":             access.role,
        "display_name":     access.display_name,
        "allowed_services": access.allowed_services,

        # from session (None if not yet created — pre-classification state)
        "session_id":           str(session.session_id) if session else None,
        "serial_number":        None,   # None until request_log is created
        "service_type_id":      str(session.service_type_id) if session and session.service_type_id else None,
        "conversation_history": session.conversation_history if session else [],
        "collected_fields":     session.collected_fields if session else {},

        # from webhook_receiver (the raw incoming message)
        "content": message["content"],
        "msg_id":  message["msg_id"],

        # filled in downstream by AI, workflow_engine, request_logger
        "parsed_input":   None,
        "request_log_id": None,
        "result":         None,
        "error_detail":   None,
    }
```

### Session exit paths

| Status | Triggered by | When |
|---|---|---|
| `completed` | Workflow Engine | All steps finished successfully |
| `cancelled` | Pipeline | User sent "取消" at any point |
| `rejected` | Pipeline | User asked for a service their group doesn't have |
| `failed` | Workflow Engine | YiDiDa / OMS / system error mid-workflow |
| `timed_out` | Background job | `expires_at` passed with no user activity |

### Scenarios that leave a session unfinished

**User goes silent:**
1. Claude asked for missing fields, user never replied → expires in 1 hour (`timed_out`)
2. Bot sent confirmation template, user never replied → expires in 1 hour (`timed_out`)
3. User read confirmation, walked away → expires in 1 hour (`timed_out`)

**User actively abandons:**
4. User types "取消" mid-conversation → `close_session(status="cancelled")` immediately
5. User types "取消" after seeing confirmation → same
6. User sends a new service request while one is in-progress → AI detects new request intent,
   pipeline rejects: "你有一个未完成的申请，请先完成或取消" — the unique index on
   `status IN ('active', 'pending_confirmation')` prevents opening a second session

**System failure:**
7. All AI providers down → session stays `active`, user notified to retry later
8. YiDiDa fails after confirm → `close_session(status="failed")`, user notified
9. OMS fails after label created → `close_session(status="failed")`, user notified
   (partial success — label exists at YiDiDa, OMS record missing; no rollback in v1)
10. Server crash → session stays in DB unchanged; background job catches it at `expires_at`

**Edge cases:**
11. Duplicate WeChat message delivery → idempotency via `wechat_msg_id` unique index drops second
12. User suspended mid-conversation → next message fails Access Control; session orphaned until expiry

### Key design notes
- **1 session at a time** — unique index on `(wechat_openid, group_id) WHERE status IN ('active', 'pending_confirmation')` enforces this at the DB level
- `add_message()` resets `expires_at` on every message — active back-and-forth never expires mid-flow
- `resolve_session()` checks serial first (fast path, no AI call), then falls back to `find_current_session()`
- **AI always decides intent** — `resolve_session()` only loads context; it never auto-routes based on session count
- `build_context()` is the single assembly point — downstream modules never query the DB for user/session data themselves

---

---

## Module 7 — ai/

**Role:** AI provider abstraction layer. Takes the pipeline context dict, calls an AI
provider, returns a structured `AIResponse`. The rest of the system never imports
`anthropic` directly — everything goes through this layer.

### Files

| File | Purpose |
|---|---|
| `ai/base.py` | `AIResponse` dataclass + `AIProvider` ABC |
| `ai/chain.py` | `AIProviderChain` — tries providers in order, falls back on exception |
| `ai/claude_provider.py` | Claude implementation (v1 primary) |
| `ai/openai_provider.py` | OpenAI placeholder (v2) |

### `AIResponse` fields

| Field | Type | Meaning |
|---|---|---|
| `intent` | str | `new_request` / `continuation` / `confirm` / `cancel` / `check_services` / `unrecognized` |
| `reply` | str | Chinese message to send to the user |
| `extracted_fields` | dict | Fields pulled from this turn only (not cumulative) |
| `all_fields_collected` | bool | True → ready to show confirmation template |
| `service_type_name` | str\|None | Set when intent == `new_request` |

### Chain wiring (in `main.py`)

```python
from ai.chain import AIProviderChain
from ai.claude_provider import ClaudeProvider
from ai.openai_provider import OpenAIProvider

ai_chain = AIProviderChain(providers=[
    ClaudeProvider(),    # primary
    OpenAIProvider(),    # fallback — raises NotImplementedError until v2
])
```

### Key design notes
- `AIProvider` is an ABC — `@property @abstractmethod name` and `@abstractmethod process()` must be implemented by every provider
- Chain catches any exception from a provider and moves to the next; if all fail, raises `RuntimeError` — caller (Workflow Engine) marks session `failed`
- Claude is instructed to always return valid JSON — `_parse_response()` handles the rare case it doesn't
- System prompt includes `allowed_services` (with input schemas), `collected_fields`, and session state so Claude knows exactly what's been gathered and what's still needed
- Claude is explicitly told NOT to generate the confirmation summary — that belongs to `confirmation.py` (Module 8)
- `check_services` intent is handled: Claude lists available services in `reply`; Workflow Engine skips the normal workflow and sends it directly

---

---

## Module 8 — core/confirmation.py

**Role:** Generates the fixed Chinese confirmation template when all required
fields have been collected. No AI involved — deterministic and auditable.

This is the "template confirms" half of the **"AI talks, template confirms"** rule.
The user is about to submit a request that costs money — the text must be consistent.

### Public function

```python
build_confirmation_message(
    service_type_name: str,
    collected_fields: dict,
    serial_number: str,
    confirmation_note: str | None = None   # from service_type.confirmation_note
) -> str
```

### Timing
The `request_log` row (and its serial number) is created by `request_logger.py`
just before this function is called — the serial must exist at call time.
This is the first moment the user sees their serial number.

### Sample output
```
请确认以下寄件信息：
申请编号：REQ-20260428-000001
服务类型：FedEx 快递标签
─────────────────
收件人姓名：John Smith
街道地址：123 Main St
城市：New York
州/省：NY
邮编：10001
重量（磅）：5.0
服务等级：PRIORITY_OVERNIGHT
包裹类型：box
─────────────────
回复"确认"提交申请，或"取消"放弃。

📌 注意：提交后标签将自动生成，运费由公司账户扣除。如需修改或取消，请立即联系管理员。
```
If `confirmation_note` is NULL, the note line is omitted entirely.

### Extending for new services
Add one entry to `_service_display_name()` and any new field keys to
`_field_label()`. Nothing else in the codebase needs to change.

---

---

## Module 9 — core/workflow_engine.py

**Role:** The main orchestrator. Receives the AI intent and drives everything
that happens next — session lifecycle, confirmation flow, workflow execution,
and user replies. No business logic lives anywhere else in the pipeline.

### Dispatch table

| Intent | Action |
|---|---|
| `new_request` | Reject if session open; otherwise create session, send AI reply |
| `continuation` | Update fields; if all collected → create request_log, send confirmation template; else send AI reply |
| `confirm` | Run workflow steps; on success → complete; on failure → fail + notify |
| `cancel` | Close session as cancelled, notify user |
| `check_services` | Send AI reply (AI already listed services) |
| `unrecognized` | Send AI reply, leave session unchanged |

### Workflow step execution

```
_run_workflow_steps()
    │
    ├── resolve workflow_id from context["allowed_services"]
    ├── load WorkflowStep rows ordered by step_order
    └── for each step:
            handler = HANDLER_REGISTRY[step.step_type]
            result  = handler.handle(context, step.config)
            context["result"].update(result)   ← accumulates for next step
```

Steps are run in sequence. Any exception aborts the chain and propagates to
`_handle_confirm`, which marks the session and request_log as `failed`.

### Key design notes
- `run()` is the single entry point — called by `api/webhook.py` inside `BackgroundTasks`
- The `reply_wechat` handler (last workflow step) sends the success message — `_handle_confirm` does not send it directly
- `confirmation_note` is fetched fresh from DB at confirmation time — not cached in context
- `context["result"]` is built up incrementally across steps so each handler can read the previous step's output (e.g. `reply_wechat` reads the tracking number set by the label handler)
- Bug fix applied to `claude_provider._build_messages`: always appends current message to history before passing to Claude

---

---

## Module 10 — core/request_logger.py

**Role:** Creates and updates `request_log` rows. Write-only — admin reads go
through `api/admin/logs.py` directly. Nothing else in the codebase touches
`request_log` directly.

### Functions

| Function | Called when |
|---|---|
| `create_log()` | All fields collected — just before confirmation template is sent |
| `mark_success()` | All workflow steps completed successfully |
| `mark_failed()` | Any workflow step raised an exception |

### Why `db.refresh()` after `create_log()`
`serial_number` is generated server-side by PostgreSQL (`generate_serial_number()`).
After `db.commit()`, the Python object doesn't yet know the value. `db.refresh(log)`
re-reads the row from the DB so `log.serial_number` is immediately available
for `build_confirmation_message()`.

---

---

## Module 11 — clients/

**Role:** Thin HTTP wrappers for each external service. Each client has one job:
make the API call, raise `RuntimeError` on failure. No business logic.

### `wechat_client.py`
- Uses `wechatpy.enterprise.WeChatClient` — handles access token fetching and 2-hour refresh automatically
- `send_message(wechat_openid, content)` — sends plain text to one user via WeChat Work API

### `yidida_client.py`
- `create_label(carrier, fields)` → `{"tracking_number": ..., "label_url": ...}`
- `carrier`: `"fedex"` or `"ups"`
- 30-second timeout. Raises `RuntimeError` on HTTP or API failure.
- **TODO in Phase 6:** adapt request body shape to match actual YiDiDa API spec.

### `oms_client.py`
- `create_record(data)` → `{"oms_record_id": ...}`
- `data` contains serial_number, tracking_number, carrier, recipient fields, label_url
- 30-second timeout. Raises `RuntimeError` on HTTP or API failure.
- **TODO in Phase 6:** adapt request body shape to match actual OMS API spec.

### Key design rules
- All `RuntimeError` exceptions propagate up to `workflow_engine._handle_confirm()`, which catches them and marks the session and request_log as `failed`
- No retries in v1 — errors surface immediately
- API keys and base URLs come from `config.py` — never hardcoded

---

---

## Module 12 — jobs/session_expiry.py

**Role:** Scheduled background job. Safety net for all "user went silent" scenarios.
Runs every 5 minutes. Finds sessions where `expires_at` has passed, closes them
as `timed_out`, and notifies the user.

### Key design notes
- Queries both `active` and `pending_confirmation` — both can time out
- Notification failure is swallowed — session is already closed in DB, the job must not crash
- Scheduled via APScheduler in `main.py` — not a FastAPI route
- The DB index `idx_session_expires` on `(expires_at) WHERE status IN ('active', 'pending_confirmation')` makes this query fast even with many sessions

---

---

## Module 13 — handlers/

**Role:** One handler per workflow step type. Each implements a single method:
`handle(context, config) → dict`. No lifecycle logic — that belongs to `workflow_engine.py`.

### Inheritance chain

```
BaseHandler (ABC)
    └── YDDLabelBaseHandler     ← shared YiDiDa label logic
            ├── FedExLabelHandler
            └── UPSLabelHandler
    └── OMSRecordHandler
    └── ReplyWeChatHandler
```

`FedExLabelHandler` and `UPSLabelHandler` are intentionally empty subclasses.
Carrier is determined by `config["carrier"]` set in `workflow_step.config`.
Separate classes exist so new carrier-specific logic can be added without touching the other.

### Handler registry

```python
HANDLER_REGISTRY = {
    "create_fedex_label": FedExLabelHandler,
    "create_ups_label":   UPSLabelHandler,
    "oms_record":         OMSRecordHandler,
    "reply_wechat":       ReplyWeChatHandler,
}
```

Every `step_type` value in `V2__seed_data.sql` must have an entry here.
Missing entry → `RuntimeError` at runtime when workflow executes.

### Data flow through handlers

```
Label handler → result: {tracking_number, label_url}
                                    ↓
OMS handler   → reads tracking_number from context["result"]
              → result: {oms_record_id}
                                    ↓
Reply handler → reads tracking_number + serial_number
              → sends success message to user
              → result: {}
```

### Key design notes
- `config` received by each handler is already merged (`step.config` + `group_service.config`) — handlers don't know or care about the source
- `YDDLabelBaseHandler` injects YiDiDa credentials (`ydd_cust_id`, `ydd_channel_id`) from config into the API call
- `OMSRecordHandler` reads `serial_number` from `context` — this is set by `workflow_engine` after `request_logger.create_log()` runs
- `ReplyWeChatHandler` is the only handler that calls `wechat_client` — all other messages are sent by `workflow_engine` directly

---

## Pending Tasks (noted for later phases)

- **"查看可用服务" command** — users should be able to @Bot and ask what services they can use.
  This is a new message intent to handle in the AI classification step. Remind when designing
  the AI Provider Chain prompt (Module 7) and the Workflow Engine intent routing (Module 9).

- **v2 — Cancel-and-switch UX** — when a user sends a new request while one session is already
  in progress, instead of rejecting directly, the bot asks: "你有一个未完成的申请（REQ-xxx），
  是否取消并开始新申请？" User answers yes/no in the next turn. Handled entirely through
  conversation_history — no new status needed. Deferred to v2 to keep v1 simple.
