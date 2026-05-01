# Admin API Reference
# Logistics WeChat Bot Platform — v1

**Base URL (Render testing):** `https://wechat-bot-atse.onrender.com`

**Auth header required on all `/admin` endpoints:**
```
X-Admin-Key: e30772b17ed76ab84f5bd029c8af26e5fd52082b225e5e3bb102e56897db0e11
```

**PowerShell shorthand** (paste at start of session):
```powershell
$base = "https://wechat-bot-atse.onrender.com"
$h    = @{"X-Admin-Key"="e30772b17ed76ab84f5bd029c8af26e5fd52082b225e5e3bb102e56897db0e11"}
```

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
| `wechat_openid` | ✅ | WeChat user ID (the `from` field in webhook messages) |
| `role` | ✅ | `"admin"` or `"customer"` |
| `display_name` | — | Name shown in bot replies and request logs |

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
      "ydd_cust_id":    "F000179",
      "ydd_api_key":    "abc12345",
      "ydd_channel_id": "Fedex home delivery 洛杉矶渠道",
      "oms_app_key":    "7067eec5f4ce4b3fa4321aabbe2623ab",
      "oms_app_secret": "0b6069240b1d49438761c3155a36ddfc",
      "oms_wh_code":    "DE19713"
    }
  }'
```
`config` keys must satisfy the service type's `group_config_schema.required` — the API validates and returns 400 if any are missing.

### Service type & workflow IDs (current)

| Service | service_type_id | Workflow | workflow_id |
|---|---|---|---|
| `fedex_label` | `a1b2c3d4-0001-0000-0000-000000000001` | `fedex_workorder` | `af000001-0000-0000-0000-000000000005` |
| `ups_label` | `a1b2c3d4-0002-0000-0000-000000000002` | *(no OMS workflow yet)* | — |
| `fedex_oms_label` | `a1b2c3d4-0003-0000-0000-000000000003` | `fedex_workorder` | `af000001-0000-0000-0000-000000000005` |

### Config keys by service type

**fedex_label** and **fedex_oms_label** (both use `fedex_workorder`):
| Key | Required | Description |
|---|---|---|
| `ydd_cust_id` | ✅ | YiDiDa login username |
| `ydd_api_key` | ✅ | YiDiDa login password |
| `ydd_channel_id` | ✅ | YiDiDa channel name (e.g. `Fedex home delivery 洛杉矶渠道`) |
| `oms_app_key` | ✅ | OMS App_Key from xlwms portal |
| `oms_app_secret` | ✅ | OMS App_Secret from xlwms portal |
| `oms_wh_code` | ✅ | OMS warehouse code fallback (e.g. `DE19713`) |
| `ydd_account_code` | — | Optional YiDiDa billing account code |

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

## One-time Migration

### Seed V6 OMS data
Idempotent — safe to call multiple times.
```powershell
Invoke-RestMethod "$base/admin/seed-v6" -Method POST -Headers $h | ConvertTo-Json -Depth 3
```
Inserts/updates: `fedex_oms_label` service type, `fedex_workorder` workflow + steps, test group service assignments with OMS credentials.

---

## Typical Onboarding Flow (New Customer Group)

```
1. GET  /admin/service-types          → note service_type_id values
2. GET  /admin/workflows              → note workflow_id values
3. POST /admin/groups                 → register the WeChat group → save group_id
4. POST /admin/groups/{id}/members    → add each customer (role: customer)
5. POST /admin/groups/{id}/members    → add yourself (role: admin)
6. POST /admin/groups/{id}/services   → assign service with credentials
   (repeat for each service the group needs)
7. PATCH /admin/groups/{id}           → set context (location presets)
```
