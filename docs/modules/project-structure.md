# Project Structure
# Logistics WeChat Bot Platform — v1

**Version:** 1.0
**Date:** 2026-04-26

---

```
# .env (never commit to git)
WECHAT_CORP_ID=ww...
WECHAT_SECRET=...          ← agent secret, used to fetch access tokens
WECHAT_AGENT_ID=...
WECHAT_TOKEN=...           ← set during webhook config
WECHAT_ENCODING_AES_KEY=...← set during webhook config
YIDIDA_API_KEY=...
YIDIDA_BASE_URL=...
OMS_API_KEY=...
OMS_BASE_URL=...
CLAUDE_API_KEY=...
CLAUDE_MODEL=claude-sonnet-4-20250514
ADMIN_API_KEY=...          ← any random string you generate
DATABASE_URL=postgresql://...
SESSION_EXPIRY_MINUTES=60
```

---

```
wechat_bot/
│
├── main.py                        ← FastAPI app entry point, registers all routes
├── config.py                      ← all environment variables in one place
├── database.py                    ← SQLAlchemy engine + session factory
│
├── models/                        ← SQLAlchemy ORM models (mirror DB tables)
│   ├── group.py                   ← GroupConfig, GroupMember, GroupService
│   ├── service.py                 ← ServiceType
│   ├── workflow.py                ← Workflow, WorkflowStep
│   ├── session.py                 ← ConversationSession
│   └── request_log.py             ← RequestLog
│
├── api/                           ← FastAPI route handlers (thin layer — no business logic)
│   ├── webhook.py                 ← GET /webhook, POST /webhook
│   ├── health.py                  ← GET /health
│   └── admin/
│       ├── groups.py              ← /admin/groups
│       ├── members.py             ← /admin/groups/{id}/members
│       ├── services.py            ← /admin/groups/{id}/services
│       ├── reference.py           ← /admin/service-types, /admin/workflows
│       ├── logs.py                ← /admin/request-logs
│       └── sessions.py            ← /admin/sessions
│
├── core/                          ← business logic — one file per module
│   ├── webhook_receiver.py        ← signature validation, XML decrypt, message extract
│   ├── session_manager.py         ← session lookup, creation, update, close
│   ├── access_control.py          ← group member check, load allowed services
│   ├── workflow_engine.py         ← step orchestration, handler dispatch
│   ├── request_logger.py          ← create and update request_log rows
│   └── confirmation.py            ← generate fixed Chinese confirmation template
│
├── ai/                            ← AI provider abstraction layer
│   ├── base.py                    ← AIProvider ABC + AIResponse dataclass
│   ├── chain.py                   ← AIProviderChain (tries providers in order)
│   ├── claude_provider.py         ← Claude API implementation
│   └── openai_provider.py         ← OpenAI placeholder (v2)
│
├── handlers/                      ← workflow step handlers
│   ├── registry.py                ← HANDLER_REGISTRY: step_type → handler class
│   ├── base.py                    ← BaseHandler ABC
│   ├── label/
│   │   ├── base.py                ← YDDLabelBaseHandler
│   │   ├── fedex.py               ← FedExLabelHandler
│   │   └── ups.py                 ← UPSLabelHandler
│   ├── oms_record.py              ← OMSRecordHandler
│   └── reply_wechat.py            ← ReplyWeChatHandler
│
├── clients/                       ← external API clients (one per external service)
│   ├── wechat_client.py           ← outbound WeChat API (send messages, @mention)
│   ├── yidida_client.py           ← YiDiDa label creation API
│   └── oms_client.py              ← OMS API
│
├── jobs/                          ← background jobs
│   └── session_expiry.py          ← checks expired sessions, notifies user + admin
│
└── middleware/
    └── admin_auth.py              ← validates X-Admin-Key header on admin routes
```

---

## Key design rules

| Rule | Reason |
|---|---|
| `api/` contains NO business logic | Route handlers only call `core/` functions — easy to test core logic independently |
| `core/` contains NO FastAPI imports | Core modules are pure Python — testable without running the web server |
| `clients/` are thin wrappers | Each client only handles HTTP calls + error formatting — no business logic |
| `handlers/` only implement `handle()` | No lifecycle logic (validate/log/reply) — that belongs in `workflow_engine.py` |
| All config comes from `config.py` | No hardcoded strings or secrets anywhere else in the codebase |
