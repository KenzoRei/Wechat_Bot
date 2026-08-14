# ADR-004: Use Claude API for conversation management and field extraction

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use Claude API (claude-sonnet-4-20250514) as the AI provider for conversation management, service classification, field extraction, and input normalization. Accessed through an adapter pattern to allow future provider substitution.

## Alternatives considered
- **Regex + rules engine** — write pattern-matching rules for every possible message phrasing; breaks constantly as users find new phrasings; requires ongoing maintenance; cannot handle natural language variation (e.g. "我要寄个联邦的" vs "FedEx label please" vs "联邦快递")
- **OpenAI GPT API** — viable alternative; not chosen as primary because Claude performs well for Chinese language tasks and structured extraction; retained as v2 fallback option in the provider chain

## Reason
- Handles natural language natively in both Chinese and English without rules maintenance
- Normalizes messy user input: splits single-line addresses, converts informal weight expressions, corrects typos
- Manages multi-turn conversation state: knows what fields are collected, what is missing, how to ask naturally
- Handles multi-session disambiguation when users reference previous requests informally
- No rules to maintain as customer phrasing evolves over time

## Adapter pattern decision
Claude is accessed through an AIProviderChain abstraction:
- v1: chain contains Claude only
- v2: OpenAI added as fallback if Claude is unavailable
- Adding a new provider requires one new class; zero changes to Workflow Engine or any other component

## Consequences
- Per-request API cost (negligible at <50 req/day)
- Response latency depends on Claude API availability
- Prompt design and system instructions must be maintained as service types are added
- If all providers fail: notify user and admin in WeChat group, close session, log failure
