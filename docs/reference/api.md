# HTTP API reference

**Status:** Current route inventory
**Owner:** Engineering
**Last verified against commit:** `aaf3191` (2026-09-11)

The generated FastAPI OpenAPI schema is the field-level authority. This document
defines route purpose, composition, and authentication. Admin examples are in
[Admin API](../operations/admin-api.md).

## Public and channel routes

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/health/live` | Liveness; no dependency checks |
| GET | `/health/ready` | Database readiness and configured-mode summary; returns 503 on failure |
| GET/POST | `/webhook` | Smart Bot verification/messages; present only when enabled |
| GET/POST | `/kefu/callback` | Kefu verification/sync events; present only when callback mode is enabled |
| GET | `/labels/{serial_number}` | Label retrieval |
| GET | `/files/download/{token}` | Tokenized artifact download |
| GET | `/admin/panel` | Browser admin client; API calls still require the admin key |

## Admin routes

All routes below require `X-Admin-Key`.

| Area | Routes |
|---|---|
| Groups | `POST/GET /admin/groups`, `PATCH /admin/groups/{group_id}` |
| Smart Bot members | create/list/update/delete under `/admin/groups/{group_id}/members` |
| Kefu staff | `GET /admin/kefu-staff`, `PATCH /admin/kefu-staff/{staff_id}` |
| Group services | create/list/delete under `/admin/groups/{group_id}/services` |
| Roles | `GET/POST /admin/roles` |
| Role service permissions (global, deny-by-default) | create/list/delete under `/admin/roles/{role_id}/services` |
| Catalog | `GET /admin/service-types`, `GET /admin/workflows` |
| Logs | `GET /admin/request-logs`, `GET /admin/request-logs/{serial_number}` |
| Sessions | `GET /admin/sessions` |
| Invoices | `GET /admin/invoices/export`, `GET /admin/invoices/export-link` |
| Customers (master data + credentials) | create/list/get/update under `/admin/customers`, credential status/set under `/admin/customers/{customer_id}/credentials` |
| Company warehouses | create/list/get/update under `/admin/warehouses` |

The previous v1 contract, including obsolete Railway/ngrok references, is
preserved as [historical](../archive/designs/api-contracts-v1.md).
