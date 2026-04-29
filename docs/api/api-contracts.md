# API Contracts
# Logistics WeChat Bot Platform — v1

**Version:** 1.0
**Date:** 2026-04-26
**Status:** Finalized

---

## General Rules

### Base URL
- Development: `https://{ngrok-url}` (changes each session)
- Production:  `https://{railway-domain}.railway.app`

### Authentication
- **Webhook endpoint:** no auth header — authenticated via WeChat signature in query params
- **All admin endpoints:** require `X-Admin-Key: {secret}` header
  - Secret stored in server environment variable `ADMIN_API_KEY`
  - Return `401` if header is missing or wrong

### Response format
All responses are JSON except the webhook (which returns plain text).

**Success:**
```json
{ "data": { ... } }
```

**Error:**
```json
{ "error": "human readable description" }
```

### HTTP status codes used
| Code | Meaning |
|---|---|
| 200 | Success |
| 201 | Created |
| 400 | Bad request — missing or invalid fields |
| 401 | Unauthorized — wrong or missing API key |
| 404 | Not found |
| 409 | Conflict — duplicate entry |
| 500 | Server error |

---

## 1. Health Check

### `GET /health`
Check if the server is running. No auth required.

**Response 200:**
```json
{
    "data": {
        "status": "ok",
        "timestamp": "2026-04-26T14:32:00Z"
    }
}
```

---

## 2. WeChat Webhook

### `GET /webhook`
WeChat calls this once during initial bot setup to verify the endpoint URL.

**Query params (sent by WeChat, not you):**
| Param | Description |
|---|---|
| `msg_signature` | WeChat's signature for validation |
| `timestamp` | Unix timestamp |
| `nonce` | Random string |
| `echostr` | Random string WeChat expects back |

**What the server does:**
1. Validate signature: `SHA1(sort([token, timestamp, nonce]))`
2. If valid → return `echostr` value as plain text (HTTP 200)
3. If invalid → return HTTP 403

**Response 200:** plain text `echostr` value

---

### `POST /webhook`
WeChat sends every group message here. This is the main entry point for all bot activity.

**Query params (sent by WeChat):**
| Param | Description |
|---|---|
| `msg_signature` | Signature for validation |
| `timestamp` | Unix timestamp |
| `nonce` | Random string |

**Request body (XML, sent by WeChat, encrypted):**
```xml
<xml>
  <ToUserName><![CDATA[ww_corp_id]]></ToUserName>
  <Encrypt><![CDATA[encrypted_content]]></Encrypt>
</xml>
```

**After decryption, the message content is:**
```xml
<xml>
  <ToUserName><![CDATA[ww_corp_id]]></ToUserName>
  <FromUserName><![CDATA[user_wechat_openid]]></FromUserName>
  <CreateTime>1234567890</CreateTime>
  <MsgType><![CDATA[text]]></MsgType>
  <Content><![CDATA[message content here]]></Content>
  <MsgId>7304158700020148225</MsgId>
  <AgentID>1</AgentID>
</xml>
```

**What the server does:**
1. Validate signature → reject if invalid (403)
2. Return HTTP 200 immediately with body `"success"` ← MUST happen within 5 seconds
3. Process message asynchronously (session lookup, access control, Claude AI, etc.)

**Response 200:** plain text `"success"`

> Note: The HTTP 200 response and the actual bot reply to WeChat are two separate things.
> The 200 just tells WeChat "we received it." The bot reply is sent later via WeChat API Client.

---

## 3. Admin — Groups

All admin endpoints require `X-Admin-Key: {secret}` header.

---

### `POST /admin/groups`
Register a new WeChat Work group.

**Request body:**
```json
{
    "wechat_group_id": "ww_group_abc123",
    "description": "NYC Customer Group A",
    "daily_request_limit": 30
}
```

> `daily_request_limit` is optional. Omit it or set to `null` for unlimited.

**Response 201:**
```json
{
    "data": {
        "group_id": "uuid-...",
        "wechat_group_id": "ww_group_abc123",
        "description": "NYC Customer Group A",
        "is_active": true,
        "daily_request_limit": 30,
        "created_at": "2026-04-26T14:32:00Z"
    }
}
```

**Error 409:** `wechat_group_id` already exists.

---

### `GET /admin/groups`
List all registered groups.

**Response 200:**
```json
{
    "data": [
        {
            "group_id": "uuid-...",
            "wechat_group_id": "ww_group_abc123",
            "description": "NYC Customer Group A",
            "is_active": true,
            "created_at": "2026-04-26T14:32:00Z"
        }
    ]
}
```

---

### `PATCH /admin/groups/{group_id}`
Update a group's description or active status.

**Request body (all fields optional — send only what you want to change):**
```json
{
    "description": "NYC Customer Group A — updated",
    "is_active": false,
    "daily_request_limit": 50
}
```

> Set `daily_request_limit` to `null` to remove the cap.

**Response 200:** updated group object (same shape as POST response)

**Error 404:** group not found.

---

## 4. Admin — Members

### `POST /admin/groups/{group_id}/members`
Add a user to a group with a role.

**Request body:**
```json
{
    "wechat_openid": "oABC123xyz",
    "role": "customer",
    "display_name": "张伟"
}
```

**Response 201:**
```json
{
    "data": {
        "wechat_openid": "oABC123xyz",
        "group_id": "uuid-...",
        "role": "customer",
        "display_name": "张伟",
        "is_active": true,
        "joined_at": "2026-04-26T14:32:00Z"
    }
}
```

**Error 409:** user already in this group.
**Error 400:** role must be `admin` or `customer`.

---

### `GET /admin/groups/{group_id}/members`
List all members of a group.

**Response 200:**
```json
{
    "data": [
        {
            "wechat_openid": "oABC123xyz",
            "role": "customer",
            "display_name": "张伟",
            "is_active": true,
            "joined_at": "2026-04-26T14:32:00Z"
        }
    ]
}
```

---

### `PATCH /admin/groups/{group_id}/members/{wechat_openid}`
Change a member's role or suspend them.

**Request body (all fields optional):**
```json
{
    "role": "admin",
    "is_active": false
}
```

**Response 200:** updated member object.
**Error 404:** member not found in this group.

---

### `DELETE /admin/groups/{group_id}/members/{wechat_openid}`
Remove a user from a group permanently.

**Response 200:**
```json
{
    "data": { "message": "member removed" }
}
```

**Error 404:** member not found in this group.

---

## 5. Admin — Group Services

### `POST /admin/groups/{group_id}/services`
Assign a service type + workflow to a group.

The `config` field must satisfy the service type's `group_config_schema`.
Check `GET /admin/service-types` for the required keys per service type.

**Request body:**
```json
{
    "service_type_id": "a1b2c3d4-0001-0000-0000-000000000001",
    "workflow_id": "wf000001-0000-0000-0000-000000000001",
    "config": {
        "ydd_cust_id":    "A001",
        "ydd_channel_id": "CH1"
    }
}
```

**Response 201:**
```json
{
    "data": {
        "group_id": "uuid-...",
        "service_type_id": "a1b2c3d4-0001-0000-0000-000000000001",
        "service_name": "fedex_label",
        "workflow_id": "wf000001-0000-0000-0000-000000000001",
        "workflow_name": "fedex_with_oms",
        "config": {
            "ydd_cust_id":    "A001",
            "ydd_channel_id": "CH1"
        }
    }
}
```

**Error 409:** this service already assigned to this group.
**Error 404:** service_type_id or workflow_id not found.
**Error 400:** `config` is missing a required key defined in `group_config_schema`.

---

### `GET /admin/groups/{group_id}/services`
List all services assigned to a group.

**Response 200:**
```json
{
    "data": [
        {
            "service_type_id": "a1b2c3d4-0001-0000-0000-000000000001",
            "service_name": "fedex_label",
            "workflow_id": "wf000001-0000-0000-0000-000000000001",
            "workflow_name": "fedex_with_oms"
        }
    ]
}
```

---

### `DELETE /admin/groups/{group_id}/services/{service_type_id}`
Remove a service from a group.

**Response 200:**
```json
{
    "data": { "message": "service removed from group" }
}
```

**Error 404:** service not assigned to this group.

---

## 6. Admin — Reference Data

### `GET /admin/service-types`
List all available service types. Use these IDs when assigning services to groups.

**Response 200:**
```json
{
    "data": [
        {
            "service_type_id": "a1b2c3d4-0001-0000-0000-000000000001",
            "name": "fedex_label",
            "description": "FedEx shipping label creation via YiDiDa",
            "is_active": true
        },
        {
            "service_type_id": "a1b2c3d4-0002-0000-0000-000000000002",
            "name": "ups_label",
            "description": "UPS shipping label creation via YiDiDa",
            "is_active": true
        }
    ]
}
```

---

### `GET /admin/workflows`
List all available workflows. Use these IDs when assigning services to groups.

**Response 200:**
```json
{
    "data": [
        {
            "workflow_id": "wf000001-0000-0000-0000-000000000001",
            "name": "fedex_with_oms",
            "description": "Create FedEx label via YiDiDa, record in OMS, reply to WeChat",
            "steps": [
                { "step_order": 1, "step_type": "create_fedex_label" },
                { "step_order": 2, "step_type": "oms_record" },
                { "step_order": 3, "step_type": "reply_wechat" }
            ]
        },
        {
            "workflow_id": "wf000001-0000-0000-0000-000000000002",
            "name": "fedex_only",
            "description": "Create FedEx label via YiDiDa, reply to WeChat — no OMS record",
            "steps": [
                { "step_order": 1, "step_type": "create_fedex_label" },
                { "step_order": 2, "step_type": "reply_wechat" }
            ]
        }
    ]
}
```

---

## 7. Admin — Request Logs

### `GET /admin/request-logs`
List request logs. Supports optional filters via query params.

**Query params (all optional, any combination):**
| Param | Type | Example | Description |
|---|---|---|---|
| `status` | string | `failed` | Filter by status: `processing`, `success`, `failed`, `timed_out` |
| `group_id` | uuid | `uuid-...` | Filter by group |
| `date_from` | date | `2026-04-01` | Requests created on or after this date. **Default: 30 days ago** |
| `date_to` | date | `2026-04-26` | Requests created on or before this date |

> `date_from` defaults to 30 days ago if not provided.
> Pass `date_from=2000-01-01` to retrieve the full history with no lower bound.

**Response 200:**
```json
{
    "data": [
        {
            "log_id": "uuid-...",
            "serial_number": "REQ-20260426-000001",
            "wechat_openid": "oABC123xyz",
            "display_name": "张伟",
            "group_id": "uuid-...",
            "service_name": "fedex_label",
            "status": "success",
            "created_at": "2026-04-26T14:32:00Z",
            "completed_at": "2026-04-26T14:32:45Z"
        }
    ]
}
```

---

### `GET /admin/request-logs/{serial_number}`
Get full detail of one specific request. Useful for debugging failures.

**Response 200:**
```json
{
    "data": {
        "log_id": "uuid-...",
        "serial_number": "REQ-20260426-000001",
        "wechat_openid": "oABC123xyz",
        "display_name": "张伟",
        "group_id": "uuid-...",
        "service_name": "fedex_label",
        "workflow_name": "fedex_with_oms",
        "status": "failed",
        "raw_message": "我要寄个联邦快递给 John Smith...",
        "parsed_input": {
            "recipient_name": "John Smith",
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "weight_lbs": 5.0,
            "service_level": "PRIORITY_OVERNIGHT",
            "package_type": "box"
        },
        "result": null,
        "error_detail": "YiDiDa API timeout after 30s",
        "created_at": "2026-04-26T14:32:00Z",
        "completed_at": "2026-04-26T14:33:00Z"
    }
}
```

**Error 404:** serial number not found.

---

## 8. Admin — Active Sessions

### `GET /admin/sessions`
List all currently active conversation sessions. Useful for monitoring.

**Response 200:**
```json
{
    "data": [
        {
            "session_id": "uuid-...",
            "wechat_openid": "oABC123xyz",
            "display_name": "张伟",
            "group_id": "uuid-...",
            "service_name": "fedex_label",
            "status": "active",
            "collected_fields": {
                "recipient_name": "John Smith",
                "street": "123 Main St"
            },
            "expires_at": "2026-04-26T15:32:00Z",
            "created_at": "2026-04-26T14:32:00Z"
        }
    ]
}
```

> Note: `conversation_history` is excluded from this response — it can be very long.
> Full session detail is available in the database directly if needed.

---

## Typical Admin Workflow — Onboarding a New Customer Group

```
1. GET  /admin/service-types          → note the service_type_id values you need
2. GET  /admin/workflows              → note the workflow_id values you need
3. POST /admin/groups                 → register the new WeChat group
4. POST /admin/groups/{id}/members    → add each customer user (role: customer)
5. POST /admin/groups/{id}/members    → add yourself (role: admin)
6. POST /admin/groups/{id}/services   → assign service + workflow
   (repeat step 6 for each service the group can use)
```
