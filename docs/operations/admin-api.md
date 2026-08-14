# Admin API Reference

**Status:** Current operational examples
**Owner:** Operations
**Last verified against commit:** `c89cf6f` (2026-08-14)
# Logistics WeChat Bot Platform — v1

**Base URL (Render testing):** `https://wechat-bot-atse.onrender.com`

**Auth header required on all `/admin` endpoints:**
```
X-Admin-Key: <your ADMIN_API_KEY>
```

**PowerShell shorthand** (paste at start of session):
```powershell
$base = "https://wechat-bot-atse.onrender.com"
$h    = @{"X-Admin-Key"="<your ADMIN_API_KEY>"}
```
Security note: a previously exposed production key was redacted and rotated on
2026-08-14. Never replace the placeholder in this document with a real key.

---

## Reference Data

### List all service types
```powershell
Invoke-RestMethod "$base/admin/service-types" -Headers $h | ConvertTo-Json -Depth 5
```
Returns all active service types with `service_type_id`, `name`, `description`, `group_config_schema`.
> Note: `input_schema` (AI field list) is not returned here — use `/admin/groups/{id}/services` to see it per group.

### List all workflows
```powershell
Invoke-RestMethod "$base/admin/workflows" -Headers $h | ConvertTo-Json -Depth 5
```
Returns all workflows with their ordered steps (`step_order`, `step_type`). Use these IDs when assigning services to groups.

---

## Roles

### List all roles
```powershell
Invoke-RestMethod "$base/admin/roles" -Headers $h | ConvertTo-Json -Depth 3
```

### Create a new role
```powershell
Invoke-RestMethod "$base/admin/roles" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"name": "warehouseman", "description": "Confirms inbound/outbound completion"}'
```
No redeploy needed — new role names become usable immediately in `POST /admin/groups/{id}/members` and the service-permission grant endpoints below.

Seeded by default: `admin`, `customer`.

---

## Groups

### Create group
```powershell
Invoke-RestMethod "$base/admin/groups" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"wechat_group_id": "wrY-xxx", "description": "NYC Customer Group A"}'
```
| Field | Required | Notes |
|---|---|---|
| `wechat_group_id` | ✅ | WeChat group chat ID from Smart Robot config |
| `description` | — | Human-readable name |
| `daily_request_limit` | — | Max requests per day (null = unlimited) |
| `context` | — | JSONB — set location presets here |

Returns: `group_id` (UUID) — save this for all subsequent calls.

### List all groups
```powershell
Invoke-RestMethod "$base/admin/groups" -Headers $h | ConvertTo-Json -Depth 5
```

### Update group
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"description": "Updated name", "is_active": true, "daily_request_limit": 50}'
```
All fields optional. Omitting a field leaves it unchanged. Setting `"context": null` clears it.

### Set the Group Robot Webhook URL
Required for the daily broadcast, monthly invoice, cross-group completion notifications, and any file attachments (Excel invoice exports) — `response_url` (the normal reply channel) cannot send files or push proactively, only reply to a live inbound message. Set up by an admin right-clicking the real WeChat group → 添加群机器人 → copy the resulting URL.
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"group_robot_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXX"}'
```
Omitted for a group = every proactive push to that group silently no-ops. Pass `null` to clear it.

### Set location presets
Location presets let the AI auto-fill shipper/recipient addresses when a customer says e.g. "从LAX寄到DE".
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{
    "context": {
      "location_presets": {
        "LAX": {
          "corp_name": "TRANS WORLD LAX",
          "name":      "Paul Yang",
          "phone":     "626-242-5505",
          "street":    "293 E REDONDO BEACH BLVD",
          "city":      "GARDENA",
          "state":     "CA",
          "zip":       "90248",
          "country":   "US"
        },
        "DE": {
          "corp_name": "TRANS WORLD DE",
          "name":      "John Smith",
          "phone":     "302-555-0000",
          "street":    "100 LOGISTICS DR",
          "city":      "WILMINGTON",
          "state":     "DE",
          "zip":       "19713",
          "country":   "US"
        }
      }
    }
  }'
```
Keys in `location_presets` are the alias names customers use (e.g. `"LAX"`, `"DE"`). The AI maps them to `shipper_*` or `recipient_*` fields based on context.

---

## Members

### Add member to group
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/members" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"wechat_openid": "transworld", "role": "admin", "display_name": "Simon"}'
```
| Field | Required | Notes |
|---|---|---|
| `wechat_openid` | ✅ | WeChat user ID (the `from` field in webhook messages) — there's no way to look this up in advance; have the person send one message in the target group first, then read it off the `[webhook] from=...` line in the server logs (or, for a *member's own* ID specifically, the bot's own self-service reply to an unregistered sender already includes it — no log-digging needed for that case) |
| `role` | ✅ | Role name — must exist in the `role` table. See "Roles" section below to list/add roles |
| `display_name` | — | Name shown in bot replies and request logs |
| `warehouse_code` | Required if `role` is `warehouseman` | `JFK` or `DE` — which warehouse this member is responsible for. 400 if omitted for a `warehouseman`. Cleared automatically if the member's role is later changed away from `warehouseman` |

### List members
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/members" -Headers $h | ConvertTo-Json -Depth 3
```

### Update member (role or suspend)
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/members/{wechat_openid}" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"role": "customer", "is_active": false}'
```

### Remove member
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/members/{wechat_openid}" -Method DELETE -Headers $h
```

---

## Group Services

### Assign service to group
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{
    "service_type_id": "a1b2c3d4-0001-0000-0000-000000000001",
    "workflow_id":     "af000001-0000-0000-0000-000000000005",
    "config": {
      "ydd_cust_id":    "<ydd-customer-id>",
      "ydd_api_key":    "<ydd-api-key>",
      "ydd_channel_id": "Fedex home delivery 洛杉矶渠道",
      "oms_app_key":    "<oms-app-key>",
      "oms_app_secret": "<oms-app-secret>",
      "oms_wh_code":    "DE19713"
    }
  }'
```
`config` keys must satisfy the service type's `group_config_schema.required` — the API validates and returns 400 if any are missing.

### Service type & workflow IDs (current)

| Service | service_type_id | Workflow | workflow_id |
|---|---|---|---|
| `fedex_label` | `a1b2c3d4-0001-0000-0000-000000000001` | `fedex_workorder` | `af000001-0000-0000-0000-000000000005` |
| `ups_label` | `a1b2c3d4-0002-0000-0000-000000000002` | `ups_only` | `af000001-0000-0000-0000-000000000004` |

`fedex_label` handles both plain labels and OMS-linked labels — `oms_outbound_order_no` is an **optional** input field. If the customer provides it, the created label's OMS work order is linked to that outbound order; if not, a plain (unlinked) work order is still created. There is no separate "OMS" service to choose between.

### Config keys by service type

**fedex_label** (`fedex_workorder` workflow):
| Key | Required | Description |
|---|---|---|
| `ydd_cust_id` | ✅ | YiDiDa login username |
| `ydd_api_key` | ✅ | YiDiDa login password |
| `ydd_channel_id` | ✅ | YiDiDa channel name (e.g. `Fedex home delivery 洛杉矶渠道`) |
| `oms_app_key` | ✅ | OMS App_Key from xlwms portal |
| `oms_app_secret` | ✅ | OMS App_Secret from xlwms portal |
| `oms_wh_code` | ✅ | OMS warehouse code fallback (e.g. `DE19713`) — used if the outbound order query returns none |
| `ydd_account_code` | — | Optional YiDiDa billing account code |

**ups_label** (`ups_only` workflow — no OMS step):
| Key | Required | Description |
|---|---|---|
| `ydd_cust_id` | ✅ | YiDiDa login username |
| `ydd_api_key` | ✅ | YiDiDa login password |
| `ydd_channel_id` | ✅ | YiDiDa channel name |
| `ydd_account_code` | — | Optional YiDiDa billing account code |

### U-Choice service type & workflow IDs (current)

`service_type_id` and `workflow_id` are identical for every U-Choice service —
1:1 mapping, no service shares a workflow with another. `config` for all of
them is `{}` (no per-group credentials needed, unlike FedEx/UPS's YiDiDa
keys).

| Service | ID (both service_type and workflow) | Role |
|---|---|---|
| `uchoice_inbound_request` | `c1000000-...-000000000001` / `c2000000-...-000000000001` | customer |
| `uchoice_outbound_request` | `...-000000000002` | customer |
| `confirm_inbound_completion` | `...-000000000003` | warehouseman |
| `confirm_outbound_completion` | `...-000000000004` | warehouseman |
| `view_storage` | `...-000000000005` | customer, warehouseman, accountant, admin |
| `view_storage_history` | `...-000000000006` | customer, warehouseman, accountant, admin |
| `adjust_storage` | `...-000000000007` | warehouseman |
| `recount_storage` | `...-000000000008` | warehouseman |
| `move_storage` | `...-000000000009` | warehouseman |
| `upsert_address` | `...-00000000000a` | customer, warehouseman |
| `role_change` | `...-00000000000b` | admin |
| `view_invoice` | `...-00000000000c` | customer, accountant |

Full UUID prefix is `c1000000-0000-0000-0000-` for `service_type_id`,
`c2000000-0000-0000-0000-` for `workflow_id` — or just `GET
/admin/service-types` / `GET /admin/workflows` and match by name, don't rely
on this table staying accurate forever.

### List services for group
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services" -Headers $h | ConvertTo-Json -Depth 5
```
Returns service name, workflow name, and full config for each assigned service.

### Remove service from group
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services/{service_type_id}" -Method DELETE -Headers $h
```

---

## Service Permission Grants (role gating)

**Deny by default:** a service assigned to a group via `POST /admin/groups/{id}/services` is invisible to every role until explicitly granted. Do this right after assigning the service, or nobody — including admins — will see it.

### Grant a role access to a service
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services/{service_type_id}/roles" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"role": "admin", "created_by": "kenzo"}'
```
`created_by` is manually supplied for now — there's no per-admin identity yet, just the one shared `X-Admin-Key`.

### List grants for a service
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services/{service_type_id}/roles" -Headers $h | ConvertTo-Json -Depth 3
```

### Revoke a role's access
```powershell
Invoke-RestMethod "$base/admin/groups/{group_id}/services/{service_type_id}/roles/{role_name}" -Method DELETE -Headers $h
```

---

## Request Logs

### List recent logs (default: last 30 days)
```powershell
Invoke-RestMethod "$base/admin/request-logs" -Headers $h | ConvertTo-Json -Depth 3
```

### Filter by status
```powershell
Invoke-RestMethod "$base/admin/request-logs?status=failed" -Headers $h | ConvertTo-Json -Depth 3
```
Valid status values: `processing`, `success`, `failed`, `timed_out`

### Filter by group and/or date range
```powershell
Invoke-RestMethod "$base/admin/request-logs?group_id={uuid}&date_from=2026-05-01" -Headers $h | ConvertTo-Json -Depth 3
```
`date_from` and `date_to` accept ISO date strings (`YYYY-MM-DD`).

### Get full detail for one request
```powershell
Invoke-RestMethod "$base/admin/request-logs/REQ-20260501-000001" -Headers $h | ConvertTo-Json -Depth 5
```
Includes `raw_message`, `parsed_input`, `result` (tracking number, label base64), `error_detail`.

### Get just the error detail
```powershell
(Invoke-RestMethod "$base/admin/request-logs/REQ-20260501-000001" -Headers $h).data.error_detail
```

---

## Active Sessions

### List in-progress sessions
```powershell
Invoke-RestMethod "$base/admin/sessions" -Headers $h | ConvertTo-Json -Depth 5
```
Shows all sessions with status `active` or `pending_confirmation` — i.e. customers currently mid-conversation with the bot.

Fields: `wechat_openid`, `display_name`, `service_name`, `status`, `collected_fields`, `expires_at`.

---

## Label Download

No auth required — the serial number acts as the token.
```
GET https://wechat-bot-atse.onrender.com/labels/REQ-20260501-000001
```
Returns the FedEx/UPS label as a PDF download.

---

## U-Choice Invoice Export

Downloads the full detail backing an invoice as `.xlsx` — Summary sheet plus
one row per contributing transaction (Transportation & Palletization,
Unpacking, Storage sheets), not just the totals the chat `view_invoice`
reply shows. Same underlying `compute_invoice()` row-selection logic as the
chat response, so the two can never silently disagree.

```powershell
Invoke-WebRequest "$base/admin/invoices/export?warehouse_code=JFK&start_month=2026-01&end_month=2026-03" `
  -Headers $h -OutFile "invoice.xlsx"
```
| Param | Required | Notes |
|---|---|---|
| `warehouse_code` | ✅ | `JFK` or `DE` |
| `start_month` | ✅ | `YYYY-MM` |
| `end_month` | — | `YYYY-MM`, defaults to `start_month` for a single-month invoice |

Plain browser URL bar won't work — it needs the `X-Admin-Key` header, which a
bare URL can't send. Use curl/PowerShell/Postman, not a pasted link.

The bot also pushes this same workbook into the group automatically whenever
anyone runs the `view_invoice` chat service — but only if that group has
`group_robot_webhook_url` set (see "Set the Group Robot Webhook URL" above),
and only as a whole-group broadcast — `response_url` (the private reply
channel) cannot send files at all, confirmed against the official docs, so
there is no way to deliver it privately to just the person who asked.

---

## Typical Onboarding Flow (New Customer Group)

```
1. GET   /admin/service-types                              → note service_type_id values
2. GET   /admin/workflows                                  → note workflow_id values
3. GET   /admin/roles                                      → note role names (add one if needed)
4. POST  /admin/groups                                     → register the WeChat group → save group_id
5. POST  /admin/groups/{id}/members                        → add each customer (role: customer)
6. POST  /admin/groups/{id}/members                        → add yourself (role: admin)
7. POST  /admin/groups/{id}/services                       → assign service with credentials
   (repeat for each service the group needs)
8. POST  /admin/groups/{id}/services/{service_type_id}/roles → grant roles access to each service
   (deny-by-default — a service is invisible to everyone until granted; repeat per role per service)
9. PATCH /admin/groups/{id}                                 → set context (location presets)
```

**For a U-Choice group specifically:**
- Step 5/6: use `warehouseman`/`accountant` roles too where applicable, and pass `warehouse_code` for any `warehouseman` — required, 400 without it.
- Step 7: U-Choice services need no `config` at all — pass `{}`. See the U-Choice service catalog table above for the 12 `service_type_id`/`workflow_id` pairs.
- MVP design is **one shared group** with all four roles as members, gated by step 8 — not separate groups per role. The original reasoning and deferred multi-tenant alternative are preserved in the [historical U-Choice design](../archive/designs/uchoice-original-design.md).
- Step 9.5 (not in the numbered list above, easy to forget): `PATCH /admin/groups/{id}` with `group_robot_webhook_url` — without it, the daily digest, monthly invoice, cross-group completion notifications, and Excel invoice exports all silently no-op for that group.
