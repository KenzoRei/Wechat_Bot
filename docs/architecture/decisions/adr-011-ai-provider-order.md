# ADR-011: OpenAI first, Claude fallback for conversation processing

**Date:** 2026-09-25
**Status:** Decided (records the implemented provider order)
**Supersedes:** ADR-004's Claude-primary provider choice

## Decision

Both WeCom channels construct the same `AIProviderChain` through
`ai.chain.build_default_chain()`. It tries `OpenAIProvider` first and
`ClaudeProvider` if the first provider raises an exception. This is the
implemented behavior in `ai/chain.py`; ADR-004 describes the earlier
Claude-primary design and remains available as history.

`OPENAI_MODEL` defaults to `gpt-4o` and `CLAUDE_MODEL` defaults to
`claude-sonnet-4-20250514` in `config.py`. Deployments may override either
model through environment variables. Kefu voice transcription uses the
separate `OPENAI_TRANSCRIBE_MODEL` setting before the conversation provider
chain processes the resulting text.

## Consequences

- Both provider credentials remain required by the current `config.py`, even
  though Claude is the fallback.
- A provider failure can change which model handles a turn; validation and
  durable request handling must remain provider-independent.
- This record describes the current order; it does not claim a measured
  quality or cost advantage for either provider.
