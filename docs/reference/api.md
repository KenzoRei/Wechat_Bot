# HTTP API reference

**Status:** Current route inventory
**Owner:** Engineering
**Last verified against commit:** `c89cf6f` (2026-08-14)

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
| Service role grants | create/list/delete under `/admin/groups/{group_id}/services/{service_type_id}/roles` |
| Roles | `GET/POST /admin/roles` |
| Catalog | `GET /admin/service-types`, `GET /admin/workflows` |
| Logs | `GET /admin/request-logs`, `GET /admin/request-logs/{serial_number}` |
| Sessions | `GET /admin/sessions` |
| Invoices | `GET /admin/invoices/export`, `GET /admin/invoices/export-link` |

The previous v1 contract, including obsolete Railway/ngrok references, is
preserved as [historical](../archive/designs/api-contracts-v1.md).
