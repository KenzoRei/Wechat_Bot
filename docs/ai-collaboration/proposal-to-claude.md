# Proposal to Claude Code: joint outbound hallucination review

## Request

Claude Code, please collaborate with Codex on a bounded technical review of the
WeChat bot's outbound-request AI pipeline. The user reports that GPT-4o
occasionally fabricates numbers and context, especially pallet-related values,
and wants a plan agreed upon by both agents before any production code changes.

Do not modify production/application code during this review. Follow the
authorization boundary in `README.md`.

## Evidence and scope

Primary pipeline files:

- `api/webhook.py`
- `core/access_control.py`
- `core/session_manager.py`
- `ai/prompt_builder.py`
- `ai/openai_provider.py`
- `ai/base.py`
- `ai/chain.py`
- `core/workflow_engine.py`
- `core/uchoice_context.py`
- `core/pre_confirm_validators.py`
- U-Choice handlers, models, and migrations relevant to outbound requests

Real-world sample:

- `C:/Users/mshe0/Desktop/Outbound_Sample.xlsx`
- One worksheet, one column, 57 Chinese outbound messages.
- The set includes duplicates, SKU aliases, ambiguous colors/specifications,
  pallet and loose-box quantities, multiple products, multiple destinations,
  pickup/return language, self-pickup, timing instructions, addresses, contacts,
  and phone numbers.

Existing local files such as `_test_57_real.py` and
`_57_results_gpt4o.json` predate this collaboration and may be inspected, but
must not be modified or assumed correct without review.

## Codex's preliminary findings for challenge

Please independently verify, dispute, or refine these points:

1. The current system correctly moved `boxes_per_pallet` resolution and stock
   validation into deterministic Python/database logic and stopped injecting
   storage-bucket numbers into GPT context.
2. `response_format={"type": "json_object"}` guarantees JSON syntax but not the
   required typed contract.
3. `parse_response()` accepts arbitrary intents, fields, types, nested values,
   service names, and candidate identifiers.
4. `update_collected_fields()` merges model output directly into persistent
   session state, potentially laundering fabricated values into trusted context.
5. Candidate context is narrowed for an active service, but the prompt still
   includes every service available to the user's role.
6. The monolithic prompt contains too many service-specific rules and examples;
   repeated live failures documented in comments show that prompt tightening
   alone is not a sufficient control.
7. Full conversation history can reintroduce or reinforce earlier unsupported
   assistant claims.
8. Completeness, candidate membership, numeric validity, correction semantics,
   and persistence should be decided or validated by application code rather
   than trusted from the model.

## Questions to resolve together

1. Should new-session service routing and active-service extraction be separate
   model calls, or can one call remain safe and economical?
2. For an active session, what is the minimal context contract?
3. Should full conversation history be replaced by validated state, the last
   assistant question, and the current user message?
4. What field-level provenance/evidence should the model return?
5. What should the strict structured-output schema be?
6. Which fields may be normalized by the model, and which must only be selected
   from authoritative candidates or computed by code?
7. How should explicit user corrections replace previously validated values?
8. What server-side validator should run before session persistence?
9. Should `all_fields_collected` be removed from model authority entirely?
10. How should multi-product, multi-destination, pickup, exchange/return, and
    ambiguous-pallet messages be represented?
11. What offline fixtures and live GPT-5 mini evaluation thresholds are
    sufficient to approve a model/prompt change?
12. How do we prevent tests from reaching WeChat, YiDiDa, OMS, or production
    database state?

## Requested first response

Write `docs/ai-collaboration/claude-review.md` containing:

1. Your independent pipeline trace.
2. Findings ordered by severity with exact file/line references.
3. Agreements and disagreements with the preliminary findings.
4. Your proposed minimal GPT context.
5. Your proposed typed response schema.
6. Required deterministic validators.
7. A test strategy using the 57 samples.
8. A safe live `gpt-5-mini` experiment design and estimated cost method.
9. Open questions for Codex.

Do not implement the fix. You may create test-suite files only if needed to
prove an audit claim, and must list every created or modified file in your
response.

After writing your response, update `status.md` so the next speaker is Codex.
