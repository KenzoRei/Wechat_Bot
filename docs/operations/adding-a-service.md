# How to Add a New Service

**Status:** Current
**Owner:** Engineering
**Last reviewed:** 2026-08-14

A checklist for adding a new bot-facing service (e.g. `role_change`, `view_invoice`)
to the platform, written from what was actually learned building the 12 U-Choice
services. Follow this order — each step depends on the one before it.

There is no code generator for any of this; every step is a manual edit to one
of a small number of well-known files. That's deliberate — the registry
pattern (`HANDLER_REGISTRY` / `CONFIRMATION_BUILDERS` / `RESULT_BUILDERS`) keeps
each new service's code isolated to its own small function, so "add a service"
never means touching a big switch statement.

---

## 1. Migration — add the `service_type` row

New numbered file, `db/migrations/V{n}__<short_description>.sql`. Never edit a
migration file after it's been applied to a real database — add a new one.

```sql
INSERT INTO service_type (service_type_id, name, description, input_schema, group_config_schema, confirmation_note, requires_confirmation, targets_existing_request) VALUES
(
    '<uuid>',
    'my_new_service',
    'One-line English description — this reaches the AI verbatim, it is not cosmetic. See "input_schema.description" note below.',
    '{
        "required": ["field_a", "field_b"],
        "optional": ["field_c"],
        "field_hints": {
            "field_a": "What this field means, and how to extract it from natural language.",
            "field_c": "Same, for the optional field."
        }
    }',
    '{}',
    '中文确认提示，显示在确认消息底部。所有面向用户的文本必须是中文——这个字段直接渲染给客户看。',
    TRUE,   -- requires_confirmation
    FALSE   -- targets_existing_request
);
```

Flags to get right up front (`ALTER TABLE service_type` already has all of
these columns — nothing new to add unless you need a genuinely new flag):

| Flag | Set `TRUE` when |
|---|---|
| `requires_confirmation` | Almost always. `FALSE` only for pure read-only queries (`view_storage`, `view_invoice`) that should execute immediately with no confirm/cancel step. |
| `targets_existing_request` | The service *completes* an existing request rather than creating a new one (e.g. `confirm_inbound_completion`). See §7 below — these need extra care. |
| `awaits_completion` | The service's own confirmation doesn't mean the job is *done* — it stays at `status='processing'` until a **separate**, later service (a `targets_existing_request` one) finishes it. `uchoice_inbound_request`/`uchoice_outbound_request` are the only two today. Get this wrong and the request silently flips to `'success'` the instant the customer confirms, before any physical work happened — this was a real bug, found by live-testing, not by inspection. |

**`description` is not decoration.** It's sent to the AI verbatim inside the
`该群可用服务` prompt block, and it's the *only* semantic signal the AI has for
matching a vague message to your new service — the `name` field is an opaque
snake_case string the model can't reason about on its own. A terse or missing
description is the single most common reason a new service silently gets
ignored in favor of `check_services` — see §8.

**`confirmation_note` must be Chinese.** Every note in the seed data was
originally written in English and had to be translated in a follow-up
migration (`V5`) once it was noticed rendering mid-Chinese-sentence in a real
confirmation message. Write it in Chinese the first time.

---

## 2. Migration — add `workflow` + `workflow_step` rows

Same file. One `workflow` row, then its ordered `workflow_step` rows. The
`step_type` strings must exactly match keys you're about to add to
`HANDLER_REGISTRY` in step 3 — there's no DB-level check tying them together,
a typo here fails at runtime with `RuntimeError: No handler registered for
step_type`, not at migration time.

```sql
INSERT INTO workflow (workflow_id, name, description) VALUES
('<uuid>', 'my_new_service', 'What this workflow does, one line.');

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
('<workflow_uuid>', 1, 'do_the_thing', '{}'),
('<workflow_uuid>', 2, 'reply_wechat', '{}');
```

`reply_wechat` is almost always the last step — it's the existing, shared
handler that renders and sends the final message (see step 5).

---

## 3. Handler class(es)

New class in `handlers/uchoice/` (or a new file if it's a new domain),
subclassing `BaseHandler`:

```python
class DoTheThingHandler(BaseHandler):
    def handle(self, context: dict, config: dict, db) -> dict:
        fields = context.get("collected_fields", {})
        # ... do the actual work, mutate the DB, whatever ...
        return {"whatever_the_response_builder_needs": ...}
```

Register it in `handlers/registry.py`'s `HANDLER_REGISTRY` dict, keyed by the
exact `step_type` string from step 2.

**What's already on `context` when your handler runs**, without you having to
fetch it again: `wechat_openid`, `group_id`, `warehouse_codes` (the
*caller's* own list, from `AccessResult` — not necessarily the request's),
`display_name`,
`role`, `collected_fields`, `serial_number`, `request_log_id`, `result`
(accumulated from prior steps in this same workflow run — mutating
`context["result"]` in an earlier step makes it visible to later ones and to
the final `reply_wechat` step). For `targets_existing_request` services,
`context["_uchoice_target"]` is also available from whichever step resolved
the target (see §7) — it's a plain mutable dict slot on `context`, not part of
the return-value-merging convention, used exactly for this kind of
step-to-step handoff.

**Reuse before you write new DB-query logic.** Check `core/uchoice_context.py`
and `core/uchoice_storage.py` first:
- `sku_label_map(db)` — resolves `sku_code → description`, needed by nearly
  every confirmation/response builder that touches `sku_lines`.
- `member_display_label`, `role_label`, `charge_type_label` (in
  `core/confirmation.py`, imported cross-file where needed) — same idea for
  member names, role names, address charge tiers.
- `apply_storage_delta()` (`core/uchoice_storage.py`) — the *only* place that
  writes `uchoice_storage`/`uchoice_storage_txn` rows. If your service
  mutates storage, compute your deltas and call this — don't write to those
  tables directly.

---

## 4. Confirmation builder (`core/confirmation.py`)

Skip this section entirely if `requires_confirmation = FALSE`.

Add a function `(collected_fields: dict, db) -> list[dict]` returning
`sections` — the shared `{"label": str | None, "type": "kv"|"list"|"raw",
"items": ...}` shape `build_confirmation_message()` renders. Register it in
`CONFIRMATION_BUILDERS` keyed by `service_type.name`. No entry needed at all
if the default flat key-value dump (`_default_sections_builder`) is good
enough — most simple, single-field services never need a custom builder.

**Conventions that came out of fixing real bugs, not stylistic preference —
follow them:**

- **Use `"list"`, not `"kv"`, for anything derived from an array field**
  (`sku_lines`, `adjustment_lines`, `move_lines`, ...). A `"kv"` dict keyed by
  a computed label will silently **drop** a second line item if two entries
  produce the same key — e.g. the same SKU at two different
  `boxes_per_pallet` values, a legitimate real case per the free-bucket
  storage model. This was a real, silent data-loss bug, found only by
  deliberately testing a same-SKU-different-bucket input.
- **Sort, don't trust field-extraction order.** `sorted(raw_lines, key=lambda
  l: (l.get("sku_code", ""), l.get("boxes_per_pallet", 0)))` before
  formatting — the AI extracts fields in whatever order the user mentioned
  them, which reads as visibly "messy" otherwise.
- **Highlight the number that matters** with WeChat's supported color tag —
  `<font color="info">...</font>` (green) for a confirmed quantity,
  `<font color="warning">...</font>` (orange-red) for something the AI
  guessed/defaulted and the user should double-check. Plain `**bold**` and
  `<font color="comment">` (gray) are also supported. Table syntax is **not**
  — confirmed against the official docs, don't reach for it.
- **Put identifying context in the section label**, not buried in every row —
  `f"入库明细（{warehouse_code} 仓）"`, not a bare `"入库明细"` with warehouse
  repeated on every line.
- **Never let a raw internal code reach the customer** — resolve `sku_code`,
  `wechat_openid`, role names, and `charge_type` to their human labels via
  the shared helpers in §3, every time, in both the confirmation and the
  response (§5). This is the same class of bug as leaking `service_type.name`
  in AI replies (§8) — a code meant for the backend, shown to a person.
- **Surface every AI assumption explicitly**, don't silently apply it — the
  largest-bucket default for a missing `boxes_per_pallet`, the
  create-vs-update mode for `upsert_address`, the computed diff for
  `recount_storage` (never the raw snapshot the user typed). If the AI
  guessed something, the human must see exactly what before confirming.
- **Trim boilerplate, but keep it re-askable.** An optional field that's
  false/absent doesn't need a line in the confirmation (`needs_unpacking:
  false` showing every time was explicitly removed) — but the AI should
  still be instructed to *ask* about it during collection, so it's never
  silently missed. Confirmation-display trimming and collection-time
  thoroughness are separate concerns; don't conflate them.

---

## 5. Response builder (`core/result_message.py`)

The counterpart to step 4 — what the user sees *after* the workflow actually
runs, via `reply_wechat` (the shared final step, already wired to dispatch
through this registry — you never touch `handlers/reply_wechat.py` itself).

Two registries here, both optional (sensible defaults exist for both):
- `_RESULT_TITLE_BUILDERS[name]`: `(service_type_name, context) -> str`. Default
  is `f"{display_name}已完成"`. Override when "已完成" is misleading — e.g. an
  `awaits_completion` service should say "已提交，等待仓库确认操作", not
  "已完成", since nothing actually finished yet.
- `RESULT_BUILDERS[name]`: `(context, db) -> list[dict]`, same `sections` shape
  as confirmation builders. Default (`_default_sections_builder`) dumps
  `context["result"]` as a flat key-value list — fine for simple scalar
  results, actively bad (raw Python `repr()` of a list-of-dicts) for anything
  with array fields. If your handler's result includes an array, write a
  real builder.

If your service mutates storage, reuse `_warehouse_storage_summary_sections(db,
warehouse_code, label_prefix)` — the shared "show the full updated warehouse
storage, sorted by SKU, with per-SKU and per-warehouse totals" block used by
every storage-mutating service's response today. Don't re-derive this query.

---

## 6. Candidate-list context injection (only if the AI needs to fuzzy-match)

Needed when a field's value should come from matching free text against live
DB data — a SKU description, an address, a pending request's serial number, a
person's name. Three places, always together:

1. `core/uchoice_context.py` — a function returning a small `list[dict]` (the
   candidates). Reuse an existing one if it already covers your data
   (`sku_catalog`, `address_candidates`, `pending_request_candidates`,
   `member_candidates`).
2. `session_manager._build_uchoice_candidates()` — add the condition that
   injects it, scoped to which service names actually need it (no point
   injecting the member list for a customer who can never reach
   `role_change`).
3. `ai/prompt_builder.py`'s `候选列表` block — add a matching-rule bullet
   telling the AI exactly what to do with it, **and a concrete worked
   example** if the rule is at all subtle. Abstract rules alone were
   repeatedly insufficient during this build — every fuzzy-matching rule that
   actually held up under testing (single-candidate auto-resolve, ordinal
   follow-ups like "后面那个", the empty-`required`-array
   `all_fields_collected` rule) only started working reliably once a literal
   before/after example was added. Don't skip straight to shipping the
   abstract rule — test it (§8), and if it's inconsistent, add the example.

---

## 7. `targets_existing_request` services need extra care

If your new service completes/updates an existing request rather than
creating one (the `confirm_*_completion` pattern):

- `workflow_engine._resolve_target_request()` already handles resolving
  `reference_serial → RequestLog` and checking it exists and is still
  `status='processing'` — you don't need to re-implement that.
- **You do need your own handler-level check that the target's actual
  service type matches what you expect.** Nothing upstream verifies
  *direction* — a warehouseman could reference an outbound request's serial
  while running an inbound-completion service, and if the two request types
  happen to share field shapes, it would silently apply the wrong-direction
  storage math with no error. This was a real, unflagged gap — see
  `handlers/uchoice/lookup_validate.py`'s `_DIRECTION_SERVICE_NAMES` check
  for the pattern to copy.
- Make `reference_serial` a `required` field (§1), not optional — leaving it
  optional was exactly why a confirmation could ever build with an empty
  `collected_fields` and show nothing to the user. The AI can still
  auto-resolve it from a single pending candidate without the user typing
  it; `required` only guarantees it's *populated* before confirmation.
- Your confirmation builder (§4) needs to resolve the target itself to show
  what will actually be applied — `core/uchoice_context.py`'s
  `resolve_completion_target(db, reference_serial)` does the lookup. Without
  this, a confirmation can render before any workflow step has run, with
  nothing to show if the user relied on a documented default (e.g. "received
  quantities default to the original request if unstated") — another real,
  previously-blank-confirmation bug.

---

## 8. Test before you push — every time, no exceptions

Everything in this codebase that touches AI behavior was wrong on the first
attempt at least once, and every fix was found by actually running it, never
by reading the prompt and reasoning about it. Test locally, against the real
model, before deploying:

```python
from ai.openai_provider import OpenAIProvider
provider = OpenAIProvider()
ctx = { ... }  # allowed_services, collected_fields, uchoice_candidates, content, conversation_history, ...
for i in range(5):
    resp = provider.process(dict(ctx))
    print(resp.intent, resp.all_fields_collected, resp.extracted_fields)
```

Run at least 3–5 trials, not one — the model is non-deterministic, and a
rule that works 1/1 can fail 2/3 on rerun (this happened, more than once,
in this exact codebase). If a rule is inconsistent, don't accept "mostly
works" — add a concrete worked example (correct output *and* an explicitly
forbidden wrong output) and retest until it's reliably 5/5.

Then test the DB side directly — real `core.confirmation`/`core.result_message`
calls against the real (or a disposable) database, not just fabricated
dicts, so sku labels, address resolution, and storage totals are exercised
for real. Clean up any test rows you create afterward.

Only once both pass should this go through the normal commit/push/deploy
flow described in the main project docs.
