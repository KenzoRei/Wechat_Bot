# Codex ↔ Claude Code collaboration

This directory is the shared, file-based communication channel for
Codex/Claude Code technical collaboration. Originally scoped to the
outbound-request hallucination review (see `agreed-plan.md`, **Phase 1**,
still-standing and signed); the user explicitly expanded the scope in
round 9 (`discussion.md`) to cover the same class of validation gap found in
other U-Choice service pipelines (see `systemic-validation-addendum.md`,
**Phase 2**), plus a work-division plan between the two agents. Round 15
added **Phase 3** (`phase3-outbound-pdf-timing.md`) — a workflow-step-
placement fix, unrelated to the hallucination/validation theme but tracked
in this same channel per explicit user direction. Each phase is a
separately signed, separately user-approved document; approving one does
not imply approval of another. Later phases build on, and must not silently
contradict, what was already agreed in earlier ones.

## File ownership

- `proposal-to-claude.md` — Codex owns this charter.
- `codex-review.md` — Codex's technical position.
- `claude-review.md` — Claude Code owns this response file.
- `discussion.md` — append-only, alternating numbered messages.
- `agreed-plan.md` — joint plan; no application-code implementation begins
  until the user explicitly approves it.
- `status.md` — current handoff and next speaker.

Neither agent may overwrite the other agent's authored review. Discussion
messages must identify author and sequence number.

## User authorization boundary

Until the user explicitly approves implementation:

Allowed:

- Read any project file.
- Read `C:/Users/mshe0/Desktop/Outbound_Sample.xlsx`.
- Create or modify files under `docs/ai-collaboration/`.
- Create or modify dedicated automated test-suite files.
- Run local, non-destructive tests.
- Make controlled calls to the real `gpt-5-mini` API using the 57 supplied
  outbound samples.

Not allowed:

- Modify production/application scripts, prompts, migrations, configuration,
  handlers, models, or deployment files.
- Apply the proposed fix.
- Call WeChat, YiDiDa, OMS, or any other operational external service.
- Create labels, work orders, inventory transactions, requests, or messages.
- Modify or overwrite the source workbook.
- Commit, push, deploy, or open a pull request.

## Live-model test boundary

- Model: `gpt-5-mini` only, unless the user separately authorizes another model.
- Dataset: at most the 57 rows in `Outbound_Sample.xlsx`.
- Maximum attempts: two calls per sample.
- Do not send API credentials, database credentials, internal group secrets, or
  unrelated customer data.
- The supplied samples contain real addresses and phone numbers. The user has
  explicitly authorized using this workbook for real GPT-5 mini tests, but raw
  samples and raw responses must not be committed to Git.
- Before a live run, estimate the maximum token cost and record it in
  `discussion.md`.
- Store any raw result artifact in a gitignored local test-output directory.
- Report request count, token usage, estimated cost, failures, and retries.

## Completion condition

The discussion is complete only when both agents explicitly agree on:

1. Root causes.
2. The context that should and should not be sent to GPT.
3. The typed response contract.
4. Server-side validation and persistence rules.
5. The regression and live-model evaluation design.
6. Acceptance thresholds.
7. A staged implementation plan.
8. Remaining disagreements or risks.

The final result is a proposal, not an implementation. The user must approve
`agreed-plan.md` before production files are changed.
