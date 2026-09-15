# Code map

**Status:** Current
**Owner:** Engineering
**Last verified against commit:** `f3492b6` (2026-09-15)

| Path | Responsibility |
|---|---|
| `main.py` | FastAPI composition, conditional channel wiring, scheduler jobs |
| `config.py` | Strict environment loading and mode validation |
| `api/` | Public webhooks/downloads/health and admin HTTP routes |
| `middleware/` | Cross-cutting request middleware — `admin_auth.py` enforces `X-Admin-Key` on every `/admin` route |
| `ai/` | Provider adapters, prompt construction, provider chain |
| `core/workflow_engine.py` | Smart Bot orchestration |
| `core/kefu_*` | Kefu sync, durable turn application, rendering and delivery |
| `core/uchoice_*` | Shared U-Choice domain behavior |
| `core/customer_directory.py` | Customer master data + encrypted OMS/YDD credentials, `billing_customer_id` resolution |
| `core/warehouse_directory.py` | Company shipping-origin directory (distinct from U-Choice's own warehouse codes) |
| `core/role_registry.py` / `core/role_policy.py` | Role classification (assignable/protected/customer-identity/warehouse-scoped) and the shared assignment-field/runtime-scope policy declarations built on it — see [ADR-010](decisions/adr-010-role-service-policy-declarations.md) |
| `core/role_identity.py` | Parses a tagged target identity (`kefu:<staff_id>` vs. a bare Smart Bot openid) |
| `handlers/` | Workflow-step implementation and registry |
| `clients/` | WeCom, Kefu, YiDiDa, OMS and other external adapters |
| `models/` | SQLAlchemy mappings |
| `jobs/` | Scheduled expiry/report/invoice work |
| `db/migrations/` | Ordered forward SQL migrations |
| `tests/` | Offline/unit suites and explicitly gated PostgreSQL integration suites |

The earlier file-by-file module specification and project tree are archived in
[archive/designs](../archive/designs/) because they duplicated source code and
had drifted from runtime behavior.
