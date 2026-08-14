# System architecture

**Status:** Current
**Owner:** Engineering
**Last verified against commit:** `c89cf6f` (2026-08-14)

The service is one FastAPI application with two independently gated WeCom
entry pipelines. Both pipelines use the same PostgreSQL domain data and shared
U-Choice handlers, but they deliberately retain different orchestration and
delivery mechanics.

```mermaid
flowchart LR
    SR["WeCom Smart Bot"] --> WH["/webhook"]
    KF["WeCom Kefu"] --> KC["/kefu/callback"]
    WH --> SWE["Smart Bot workflow engine"]
    KC --> KS["Durable Kefu sync and workers"]
    SWE --> SH["Shared validators and handlers"]
    KS --> SH
    SH --> DB[(PostgreSQL)]
    SH --> EXT["YiDiDa / OMS"]
    SWE --> SRD["Smart Bot response/webhook delivery"]
    KS --> KQD["Durable Kefu outbound queue"]
```

## Runtime composition

- `main.py` is the composition root and owns route and scheduler wiring.
- `SMART_ROBOT_ENABLED` controls the Smart Bot webhook and its scheduled
  reports.
- `KEFU_CALLBACK_ENABLED` controls Kefu callback verification and routing.
- `KEFU_ENABLED` controls Kefu business processing and requires the callback
  mode.
- `RUN_SCHEDULER` identifies the single process allowed to run scheduled jobs.

See [Runtime modes](runtime-modes.md) for supported combinations and required
credentials.

## Shared domain layer

Both channels converge on shared validation, confirmation/result construction,
access control, service catalogs, and handler dispatch. Kefu retains its own
transaction-owning turn adapter because its durable processing and replay
requirements differ from Smart Bot's request lifecycle.

Important boundaries:

- Channel adapters may format and transport messages differently.
- Shared business rules must not depend on transport-specific identity shapes.
- Kefu must not call Smart Bot helpers that commit independently inside a Kefu
  transaction.
- Administrative role invariants count active administrators across both
  `group_member` and `kefu_staff`.

## Persistence and background work

- SQLAlchemy is the runtime ORM.
- PostgreSQL is the supported persistent database.
- Schema changes are sequential SQL files in `db/migrations/`; no migration
  runner or applied-version ledger currently exists.
- APScheduler runs in-process. The supported deployment topology has exactly
  one scheduler-bearing process; there is no distributed leader election.

## External systems

- WeCom Smart Bot and WeCom Kefu
- Anthropic Claude and OpenAI provider chain
- YiDiDa and OMS logistics APIs
- Render web service and PostgreSQL

## Related documentation

- [Product scope](../product/overview.md)
- [Data model](data-model.md)
- [Code map](code-map.md)
- [Configuration reference](../reference/configuration.md)
- [API reference](../reference/api.md)
- [Render operations](../operations/deployment-render.md)
- [Architecture decisions](decisions/)
