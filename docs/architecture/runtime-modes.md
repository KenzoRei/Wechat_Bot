# Runtime modes

**Status:** Current
**Owner:** Engineering and operations
**Last verified against commit:** `c89cf6f` (2026-08-14)

Channel selection is explicit. Credentials do not implicitly enable a channel.
Boolean flags accept only `true` or `false` (case-insensitive); invalid values
fail startup.

| Mode | Smart Bot | Kefu callback | Kefu processing | Purpose |
|---|---:|---:|---:|---|
| Smart Bot only | true | false | false | Internal group-chat pipeline |
| Kefu callback bootstrap | false | true | false | Verify callback before full Kefu credentials are available |
| Kefu only | false | true | true | Kefu business processing |
| Both | true | true | true | Parallel supported channels |
| Admin/health only | false | false | false | No WeCom ingress |

`KEFU_ENABLED=true` with `KEFU_CALLBACK_ENABLED=false` is invalid and fails
startup.

## Composition effects

| Flag | Routes/components controlled |
|---|---|
| `SMART_ROBOT_ENABLED` | `/webhook`, Smart Bot AI construction, daily digest and invoice jobs |
| `KEFU_CALLBACK_ENABLED` | `/kefu/callback` and callback cryptography |
| `KEFU_ENABLED` | Kefu client, sync worker, case processor, delivery worker |
| `RUN_SCHEDULER` | Starts or suppresses this process's in-process scheduler |

The session-expiry job is channel-neutral and branches only when delivering an
expiry notification.

## Switching procedure

1. Set the desired flags explicitly in the deployment environment.
2. Ensure only the selected modes' credentials are present and valid.
3. Keep exactly one scheduler owner.
4. Deploy and check `/health/live` and `/health/ready`.
5. Confirm disabled routes return 404.
6. Send a real signed test message through each enabled channel.
7. Observe logs, queue state, and request/session records during the rollback
   window.
8. Do not delete the losing pipeline during the initial observation period.

See [Configuration](../reference/configuration.md) for required variables.
