# Admin API Reference

**Status:** Current operational examples
**Owner:** Operations
**Last verified against commit:** `f3492b6` (2026-09-15)
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

### Delete a role
```powershell
Invoke-RestMethod "$base/admin/roles/{role_id}" -Method DELETE -Headers $h
```
409 if the role is protected (`admin`, `pending`) or still assigned to any
group member/Kefu staff row.

Each role in the response also carries a `required_fields` list —
descriptors (`field_name`, `label`, `value_type`, `choice_source`) for
whichever assignment-level fields that role requires (`warehouse_codes`
for `warehouseman`/`warehouse_admin`, `billing_customer_id` for
`customer`/`fedex_label_agent`). This is what the admin panel reads to
decide which inputs to show/require per role — never hardcode a role name
client-side to make that decision. See
[ADR-010](../architecture/decisions/adr-010-role-service-policy-declarations.md).

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
| `warehouse_codes` | Required if `role` is `warehouseman` or `warehouse_admin` | Array of one or more codes from `JFK`, `DE`, `NJ` — which warehouse(s) this member is responsible for. 400 if empty/omitted for a warehouse-scoped role, or if any code is unknown. Cleared automatically if the member's role is later changed away from a warehouse-scoped role |

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

## Kefu Staff

The Kefu-channel counterpart to Members above — every person who has ever
self-registered on the Kefu channel (`注册成员`), whether they end up staff
(`warehouseman`, `admin`, ...) or a customer-identity role
(`customer`, `fedex_label_agent`). There's no separate "add" endpoint here —
registration happens conversationally; admins only promote/update/remove.

### List Kefu staff
```powershell
Invoke-RestMethod "$base/admin/kefu-staff" -Headers $h | ConvertTo-Json -Depth 3
Invoke-RestMethod "$base/admin/kefu-staff?pending_only=true" -Headers $h | ConvertTo-Json -Depth 3
```

### Update Kefu staff (role, warehouse, billing customer, or suspend)
```powershell
Invoke-RestMethod "$base/admin/kefu-staff/{staff_id}" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"role": "warehouse_admin", "warehouse_codes": ["JFK", "NJ"]}'
```
Same field rules as Members above — `warehouse_codes` required for a
warehouse-scoped role, `billing_customer_id` required for a customer-identity
role, both cleared automatically when the role changes away from needing them.

### Remove Kefu staff
```powershell
Invoke-RestMethod "$base/admin/kefu-staff/{staff_id}" -Method DELETE -Headers $h
```
409 if the staff row has case history (`case_turn`/`kefu_outbound_delivery`
references it) — deactivate (`is_active: false`) instead of deleting in that
case.

### Refresh display names from WeCom
```powershell
Invoke-RestMethod "$base/admin/kefu-staff/refresh-names" -Method POST -Headers $h
```
Backfills `display_name` for every registered Kefu contact from WeCom's real
nickname (`kf/customer/batchget`) — the only source of truth for it, since
nothing else in this app ever learns a Kefu contact's actual name. 503 if Kefu
isn't configured on this deployment.

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
| `cancel_inbound_request` | `...-000000000010` | customer, admin |
| `cancel_outbound_request` | `...-000000000011` | customer, admin |
| `confirm_inbound_completion_batch` | `...-000000000012` | same roles as `confirm_inbound_completion` (V33); Kefu only |
| `confirm_outbound_completion_batch` | `...-000000000013` | same roles as `confirm_outbound_completion` (V33); Kefu only |

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

## Role Service Permissions (role gating, global)

**Deny by default, and global — not per-group.** A role has zero access to
any service until a matching row exists here, regardless of which group a
caller belongs to. This replaced the old per-group
`/admin/groups/{group_id}/services/{service_type_id}/roles` endpoints in
`V30` — real tenant differentiation still lives in `group_service` (whether
a *group* has the service enabled at all, and its per-group config); a
role's actually-reachable services are the intersection of both. See
[Data model](../architecture/data-model.md#authorization-model).

### Grant a role access to a service
```powershell
Invoke-RestMethod "$base/admin/roles/{role_id}/services/{service_type_id}" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"created_by": "kenzo"}'
```
`role_id` is the role's UUID (from `GET /admin/roles`), not its name.
`created_by` is manually supplied for now — there's no per-admin identity
yet, just the one shared `X-Admin-Key`. 409 if already granted; also 409
if granting a warehouse-scoped service to a warehouse-scoped role would
give an already-existing, incompletely-provisioned assignment (missing
`warehouse_codes`) reachability to it — assign `warehouse_codes` to those
members/staff first, then retry.

### List a role's granted services
```powershell
Invoke-RestMethod "$base/admin/roles/{role_id}/services" -Headers $h | ConvertTo-Json -Depth 3
```

### Revoke a role's access
```powershell
Invoke-RestMethod "$base/admin/roles/{role_id}/services/{service_type_id}" -Method DELETE -Headers $h
```
404 if the role didn't have that grant.

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
| `warehouse_code` | ✅ | `JFK`, `DE`, or `NJ` |
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

## Customers (master data + label credentials)

`customer_id` is the natural key (`F######`, validated against the DB CHECK
constraint). Holds profile data plus per-carrier `ydd_channel_id`/
`rate_multiplier` and encrypted OMS/YDD credentials. A `GroupMember` with
role `customer` binds to one of these via `billing_customer_id`.

### Create / list / get / update
```powershell
Invoke-RestMethod "$base/admin/customers" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"customer_id": "F000123", "display_name": "Acme Co", "created_by": "kenzo"}'

Invoke-RestMethod "$base/admin/customers" -Headers $h | ConvertTo-Json -Depth 5
Invoke-RestMethod "$base/admin/customers/F000123" -Headers $h | ConvertTo-Json -Depth 5

Invoke-RestMethod "$base/admin/customers/F000123" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"status": "active", "oms_wh_code": "DE19713", "updated_by": "kenzo"}'
```
`status` is one of `pending`, `active`, `inactive`. `?status=active` filters
the list.

### Credentials (write-only — never read back)
```powershell
# Status only -- which credential types are set and when, never the value
Invoke-RestMethod "$base/admin/customers/F000123/credentials" -Headers $h | ConvertTo-Json -Depth 3

# Set/rotate one credential
Invoke-RestMethod "$base/admin/customers/F000123/credentials" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{"credential_type": "oms_app_key", "value": "<secret>", "updated_by": "kenzo"}'
```
`credential_type` is one of `oms_app_key`, `oms_app_secret`, `ydd_username`,
`ydd_password`. Values are AES-256-GCM-encrypted at rest; no endpoint ever
returns a decrypted or encrypted value.

---

## Company Warehouses

The company's own physical shipping-origin directory (`JFK`/`DE`/`LAX`/
`ORD`/`NJ`) — distinct from U-Choice's `warehouse_codes` concept (`JFK`/
`DE`/`NJ` only). Injected into AI context so a bare abbreviation in a label
request (e.g. "从LAX到DE") resolves to a full shipper or recipient address;
the AI decides which side based on phrasing, since our warehouse can be
either sender or receiver of a given shipment.

### Create / list / get / update
```powershell
Invoke-RestMethod "$base/admin/warehouses" -Method POST -Headers $h `
  -ContentType "application/json" `
  -Body '{
    "warehouse_abbr": "LAX", "company_name": "TWF-LAX",
    "addr": "293 E REDONDO BEACH BLVD", "city": "GARDENA", "state": "CA", "zip_code": "90248",
    "contact": "Paul Yang", "phone": "626-242-5505", "created_by": "kenzo"
  }'

Invoke-RestMethod "$base/admin/warehouses" -Headers $h | ConvertTo-Json -Depth 5
Invoke-RestMethod "$base/admin/warehouses/LAX" -Headers $h | ConvertTo-Json -Depth 5

Invoke-RestMethod "$base/admin/warehouses/LAX" -Method PATCH -Headers $h `
  -ContentType "application/json" `
  -Body '{"phone": "626-242-5506", "updated_by": "kenzo"}'
```
`warehouse_abbr` is the natural key and can't be changed after creation.
`company_name` is the legal/billing entity operating that location (varies
per warehouse — most are `TWF-*`, NJ is `TWW`) and maps to
`shipper_corp_name`/`recipient_corp_name` in label requests.

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
8. POST  /admin/roles/{role_id}/services/{service_type_id} → grant roles access to each service
   (deny-by-default and GLOBAL — a service is invisible to every role until granted, once, for
   that role; not repeated per group. Repeat per role per service only)
9. PATCH /admin/groups/{id}                                 → set context (location presets)
```

**For a U-Choice group specifically:**
- Step 5/6: use `warehouseman`/`warehouse_admin`/`accountant` roles too where applicable, and pass `warehouse_codes` (a list) for any warehouse-scoped role — required, 400 without it.
- Step 7: U-Choice services need no `config` at all — pass `{}`. See the U-Choice service catalog table above for the 12 `service_type_id`/`workflow_id` pairs.
- MVP design is **one shared group** with all four roles as members, gated by step 8 — not separate groups per role. The original reasoning and deferred multi-tenant alternative are preserved in the [historical U-Choice design](../archive/designs/uchoice-original-design.md).
- Step 9.5 (not in the numbered list above, easy to forget): `PATCH /admin/groups/{id}` with `group_robot_webhook_url` — without it, the daily digest, monthly invoice, cross-group completion notifications, and Excel invoice exports all silently no-op for that group.
