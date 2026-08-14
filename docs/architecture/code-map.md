# Code map

**Status:** Current
**Owner:** Engineering
**Last verified against commit:** `c89cf6f` (2026-08-14)

| Path | Responsibility |
|---|---|
| `main.py` | FastAPI composition, conditional channel wiring, scheduler jobs |
| `config.py` | Strict environment loading and mode validation |
| `api/` | Public webhooks/downloads/health and admin HTTP routes |
| `ai/` | Provider adapters, prompt construction, provider chain |
| `core/workflow_engine.py` | Smart Bot orchestration |
| `core/kefu_*` | Kefu sync, durable turn application, rendering and delivery |
| `core/uchoice_*` | Shared U-Choice domain behavior |
| `handlers/` | Workflow-step implementation and registry |
| `clients/` | WeCom, Kefu, YiDiDa, OMS and other external adapters |
| `models/` | SQLAlchemy mappings |
| `jobs/` | Scheduled expiry/report/invoice work |
| `db/migrations/` | Ordered forward SQL migrations |
| `tests/` | Offline/unit suites and explicitly gated PostgreSQL integration suites |

The earlier file-by-file module specification and project tree are archived in
[archive/designs](../archive/designs/) because they duplicated source code and
had drifted from runtime behavior.
