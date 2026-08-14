# Current phase: Smart Robot / Kefu parity

## State

The prior phase ("deterministic Kefu operational responses") is complete,
signed, implemented, mutually cross-reviewed, and shipped. Its full record
is archived at
[`archive/discussion-rounds-103-124.md`](archive/discussion-rounds-103-124.md)
and [`archive/status-through-round-124.md`](archive/status-through-round-124.md).
This phase (round 125+, active in [`discussion.md`](discussion.md)) is
unrelated in trigger but built on top of that same architecture and the 11
further commits that landed on top of it since (listed in full in round
125) — none of which touch the signed architecture itself.

## Trigger

The user's Kefu account was blocked by WeCom's platform-side risk-control
system — confirmed via WeCom's own error text on account-management API
calls (`"微信客服当前处于联合版模式，而调用接口的access_token是通过独立版
secret获取的"`) and independently consistent with `work.weixin.qq.com/nl/norm`'s
"频繁注册、批量注册" (frequent/batch registration) prohibition, which the
account-recreation attempt sequence closely matches. This is a WeCom
account-state issue, not a defect in this codebase, and not fixable from
here — the user is pursuing it through WeCom support separately, duration
unknown.

## Goal

Make `core/workflow_engine.py` (the Smart Robot channel — internal WeCom
group chats, `@bot` mentions, `api/webhook.py`) usable as a functional
fallback for U-Choice warehouse operations, at parity with everything
`core/kefu_turn_apply.py` (the Kefu channel — customer-facing 1:1 chat
links) has independently learned since the two pipelines diverged. The two
never share turn-application code by design (`kefu_turn_apply.py`'s own
docstring: it never calls `workflow_engine.py`), though they do share
`core/pre_confirm_validators.py`, `core/result_message.py`,
`core/confirmation.py`, and `handlers/uchoice/*`.

## Findings (independently confirmed by both agents, rounds 125-127)

**Real correctness gap**, same bug class as commit `38c812a`'s Kefu fix:
`workflow_engine.py` lines 268 and 939 still contain
`ai_response.all_fields_collected or ...` — the AI's raw, pre-sanitization
claim can bypass a missing-field check the same way it did in Kefu before
that fix.

**Codex's important extension (round 126, verified by Claude Code round
127 against real DB data)**: unlike Kefu, Smart Robot has no generic
post-sanitization `input_schema.required` readiness predicate. Deleting
only `ai_response.all_fields_collected` while keeping `auto_resolved`/
`_outbound_required_fields_present` would strand FedEx/UPS and most other
services — verified live: both have `targets_existing_request=False` and
13 required fields each, and both existing overrides are unconditionally
`False` for them. The plan adds a generic schema-required predicate
(mirroring Kefu's `_all_required_fields_present`) as the new general
authority at both branch points, alongside the existing overrides
(unchanged).

**Minor UX gap**: `workflow_engine.py`'s `_handle_cancel` doesn't name the
request in its cancellation message, unlike Kefu's — fix reuses the
`owns_log` boolean the function already computes.

**Confirmed already shared / no action needed**: `pre_confirm_validators.py`
fixes (confirmed zero `source_channel` references — channel-neutral),
storage-listing grouping/zero-filtering, and the loose-pick
outbound-completion resolver (Smart Robot had this first; Kefu's version
was ported from it).

**Confirmed intentionally different by design, do not port**: session-
conflict detection, the admin purge command, invoice-as-chat-file delivery,
and `check_services` AI-authored-vs-deterministic rendering — each has a
structural reason tied to the channel it's built for.

Full detail, evidence, and exact file/line citations are in rounds 125-127
of [`discussion.md`](discussion.md) — this file is a compressed index, not
a substitute for it. The concrete signed plan is
[`smart-robot-kefu-parity-plan.md`](smart-robot-kefu-parity-plan.md).

## Required sequence

1. ~~Codex independently confirms or extends Claude Code's round-125
   findings against the current repository (not on faith).~~ **Done, round
   126** — found and the plan addresses a real gap a naive port would have
   missed.
2. ~~Both agents jointly draft and sign a concrete implementation +
   regression-test plan.~~ **Done** — `smart-robot-kefu-parity-plan.md`
   signed by Codex (round 126) and independently verified/signed by Claude
   Code against real DB schema data (round 127).
3. ~~The user explicitly authorizes implementation of the signed plan.~~
   **Done**, approved 2026-08-13.
4. ~~Production files changed.~~ **Done** — `core/workflow_engine.py`
   implemented by Claude Code, independently reviewed and tested by Codex
   (`tests/test_smart_robot_parity.py`), reciprocally reviewed by Claude
   Code (round 129, no issues). 334 offline + 74 real-Postgres, all green.

**Phase complete.** Only remaining step: the user's separate commit/push
authorization (not implied by implementation approval, same standing rule
as every prior phase).

Commit, push, deployment, and any operational API call remain separately
authorized actions, same standing rule as every prior phase. Kefu API calls
specifically are also currently blocked at the platform level regardless of
any authorization here.

## Relevant history

- Full chronological discussion through round 102:
  [`archive/discussion-rounds-001-102.md`](archive/discussion-rounds-001-102.md)
- Full chronological discussion, rounds 103-124:
  [`archive/discussion-rounds-103-124.md`](archive/discussion-rounds-103-124.md)
- Former detailed handoffs:
  [`archive/status-through-round-102.md`](archive/status-through-round-102.md),
  [`archive/status-through-round-124.md`](archive/status-through-round-124.md)
- Signed Kefu migration plan: [`kefu-migration-plan.md`](kefu-migration-plan.md)
- Signed deterministic-response plan: [`kefu-deterministic-response-plan.md`](kefu-deterministic-response-plan.md)
- Current decision index: [`decisions.md`](decisions.md)
