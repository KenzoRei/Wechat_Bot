# Configuration reference

**Status:** Current
**Owner:** Engineering and operations
**Last verified against commit:** `c89cf6f` (2026-08-14)

Secrets belong in deployment environment variables and a team password
manager—never source, docs, examples, logs, or test fixtures.

## Runtime modes

| Variable | Default | Meaning |
|---|---|---|
| `SMART_ROBOT_ENABLED` | `true` | Mount and construct Smart Bot processing |
| `KEFU_CALLBACK_ENABLED` | `true` | Mount Kefu callback verification |
| `KEFU_ENABLED` | `false` | Enable Kefu business processing/workers |
| `RUN_SCHEDULER` | `true` | This process owns scheduled jobs |
| `WORKER_INSTANCE_ID` | generated | Stable worker identity; set explicitly in deployed multi-process environments |

## Always required

- `YIDIDA_BASE_URL`
- `OMS_BASE_URL`
- `CLAUDE_API_KEY`
- `OPENAI_API_KEY`
- `ADMIN_API_KEY`
- `DATABASE_URL`

Optional general settings include `CLAUDE_MODEL`, `OPENAI_MODEL`,
`SESSION_EXPIRY_MINUTES`, and `SERVER_BASE_URL`.

## Test-only configuration

`TEST_DATABASE_URL` is consumed by the test harness and migration runner, not
the deployed application. Supply it explicitly when running PostgreSQL tests;
do not set it on Render. See
[Local PostgreSQL test database](../testing/local-postgresql.md).

## Conditional WeCom settings

`WECHAT_CORP_ID` is required when any WeCom channel/callback mode is enabled.

Smart Bot additionally requires:

- `WECHAT_TOKEN`
- `WECHAT_ENCODING_AES_KEY`

Kefu callback mode additionally requires:

- `WECHAT_KEFU_TOKEN`
- `WECHAT_KEFU_ENCODING_AES_KEY`

Full Kefu processing additionally requires:

- `WECHAT_KEFU_SECRET`
- `WECHAT_KEFU_OPEN_KFID`
- `KEFU_GROUP_ID`

Legacy variables `WECHAT_SECRET`, `WECHAT_AGENT_ID`, `WECHAT_BOT_ID`, and
`WECHAT_BOT_SECRET` are not consumed by the current application.
