# Codex review: outbound context and hallucination controls

## Position

GPT should be treated as an untrusted extraction and candidate-matching
component, not as a trusted updater of workflow state.

## Current strengths

- Role and group access are resolved before the model call.
- Active-session U-Choice candidates are scoped to the selected service.
- Real storage bucket numbers are no longer exposed to GPT for outbound request
  creation.
- `boxes_per_pallet` is re-derived and validated against current database state.
- Outbound completeness has a deterministic backstop.
- A human confirmation remains required before outbound execution.

## Principal risks

1. JSON-object mode is not a strict schema.
2. Parsed model fields are merged into session state without a universal
   service-schema/type/candidate/evidence validation boundary.
3. Active sessions still receive unrelated service definitions.
4. The prompt mixes routing, extraction, conversation policy, fuzzy matching,
   normalization, completion judgment, and many workflow-specific exceptions.
5. Stored raw conversation history can amplify earlier model mistakes.
6. The model reports values without field-level provenance.
7. Existing deterministic protection is strongest for outbound pallet buckets,
   but not uniformly applied to every service and field.

## Proposed direction

### Stage A: route a new message

Send only:

- Current message.
- User role.
- Short names/descriptions/keywords for permitted services.
- Active-session indicator.

Return only a typed intent and permitted service name. A deterministic fast path
may bypass the model for exact unambiguous commands.

### Stage B: extract for one active service

Send only:

- One active service contract.
- Server-validated collected state.
- Server-computed missing fields.
- Relevant authoritative candidates.
- Relevant group presets.
- Last assistant question.
- Current user message.

Do not send unrelated service schemas, storage bucket quantities, credentials,
internal workflow configuration, or full history by default.

### Proposed field update shape

Each proposed update should include:

- Field path.
- Typed value.
- Source enum: `current_message`, `candidate_list`, `group_preset`, or
  `validated_session`.
- Evidence from the current message when applicable.
- Candidate ID when a candidate was selected.

There should be no generic `inferred` source.

### Persistence boundary

Before persistence, code must:

- Reject unknown field paths.
- Validate types, ranges, enums, and nested collection shapes.
- Verify candidate IDs against the exact candidates sent.
- Require direct evidence for user-supplied numeric values.
- Distinguish additions from explicit corrections.
- Recompute missing fields and completion.
- Store only accepted updates.
- Keep raw model output separately for debugging if retention policy permits.

The model should not control `all_fields_collected`.

## Evaluation principle

The 57 real samples should become a reviewed fixture set with explicit expected
properties. Exact expected output is inappropriate for every conversational
reply, but critical invariants should be machine-checked:

- No unsupported numeric values.
- No nonexistent SKU/address/request IDs.
- No inventory bucket values sourced from the model.
- No missing required clarification when ambiguity is real.
- No operational side effects.
- Stable structured output.

This is a preliminary position for Claude Code to challenge.
