# Configuration reference

**Status:** Current
**Owner:** Engineering and operations
**Last verified against commit:** `3def0eb` (2026-09-25)

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
- `CUSTOMER_CREDENTIAL_KEY` — AES-256-GCM key encrypting OMS/YDD credentials
  in `customer_credential` (see `core/customer_directory.py`)
- `DATABASE_URL`

Optional general settings include `CLAUDE_MODEL`, `OPENAI_MODEL`,
`SESSION_EXPIRY_MINUTES`, and `SERVER_BASE_URL`.

## Kefu voice input (optional)

Voice messages from Kefu staff are transcribed with OpenAI (`core/kefu_voice.py`,
`core/voice_transcription.py`). All of these are optional:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-transcribe` | Transcription model. `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` and `whisper-1` are scheduled for removal on 2027-02-26 |
| `OPENAI_TRANSCRIBE_API_KEY` | `OPENAI_API_KEY` | A transcription-only key (least privilege). If unset, the main key must allow the transcription model and the audio endpoint |
| `VOICE_ALERT_DAILY_CLIPS` | `500` | Log one WARNING per UTC day when successful transcriptions reach this count; `0` disables |
| `VOICE_ALERT_DAILY_MINUTES` | `60` | Same, for total transcribed audio minutes; `0` disables |

The alerts are log-only and never block. If the OpenAI project restricts
models, the transcription model must be on its allowed list.

`config.py` parses these as `int(os.getenv(name) or default)`: the fallback
applies to the raw string, so an unset or **empty** variable gives the
default, while an explicit `0` is kept and disables that threshold. This is
pinned by `tests/kefu_integration/test_kefu_voice.py::test_usage_alert_is_on_by_default_and_zero_disables`.

## Test-only configuration

`TEST_DATABASE_URL` is consumed by the test harness and migration runner, not
the deployed application. Supply it explicitly when running PostgreSQL tests;
do not set it on Render. See
[Local PostgreSQL test database](../testing/local-postgresql.md).

## Conditional WeCom settings

`WECHAT_CORP_ID` is required when any WeCom channel/callback mode is enabled.

Smart Bot additionally requires:

- `WECHAT_BOT_TOKEN`
- `WECHAT_BOT_ENCODING_AES_KEY`

Kefu callback mode additionally requires:

- `WECHAT_KEFU_TOKEN`
- `WECHAT_KEFU_ENCODING_AES_KEY`

Full Kefu processing additionally requires:

- `WECHAT_KEFU_SECRET`
- `WECHAT_KEFU_OPEN_KFID`
- `KEFU_GROUP_ID`

Legacy variables `WECHAT_SECRET`, `WECHAT_AGENT_ID`, `WECHAT_BOT_ID`,
`WECHAT_BOT_SECRET`, `WECHAT_TOKEN`, and `WECHAT_ENCODING_AES_KEY` (renamed to
`WECHAT_BOT_TOKEN`/`WECHAT_BOT_ENCODING_AES_KEY` above) are not consumed by
the current application.
