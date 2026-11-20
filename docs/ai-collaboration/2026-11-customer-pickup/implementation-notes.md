# Customer pickup: implementation notes (for Codex's post-implementation audit)

Plan: `plan.md` rev 11 (signed by Codex and Claude, approved by the user 2026-11-20).
Branch `feature/customer-pickup`. Version 1.11.0 (1.10.0 is taken by the unmerged scheduled-outbound-reservation branch). Migration V42.

## Commits
| Commit | Scope |
|---|---|
| `7ed2d2c` | Phase 1: V42, `UchoiceAddress.pickup_kind`, `core/pickup.py`, validators, candidate exclusion, sanitizer hook |
| `aa1fdf9` | Phase 2a: turn hook, creation marker, pivot gate, execution-time check on both channels |
| `acfd1ac` | Phase 2b: prompt rules, `upsert_address` guard for system-managed rows |
| `0160112` | Phase 3a: shared pickup wording in confirmations, lists, batch labels, customer copy |
| `031fabf` | Phase 3b: PDF REMARK bar + CJK consignee, invoice destination cell, golden PDFs |
| `a4f5421` | Changelog, version |

## Where the code follows the plan
- Fields live in `extracted_fields` (`pickup_type`, `pickup_customer_name`); no `AIResponse` or parser change.
- Sanitizer (`core/uchoice_field_sanitization.py` -> `core/pickup.sanitize_extracted`) drops invalid values and any AI-written pickup-row `destination_address_id`.
- `core/pickup.apply_turn` runs after the merge and before address matching: Smart Robot `_apply_pickup_step` (new request + continuation), Kefu `apply_kefu_turn` (before the `destination_address_id` check).
- Pivot gate: `workflow_engine._maybe_pivot_to_add_address` uses the effective mode (this turn's type, else persisted). The documented D12 exception (delivery revision to an unknown address pivots on Smart Robot) is asserted in `tests/uchoice_lifecycle/test_pickup_smart_robot.py`.
- Grandfathering: `_pickup_v` is stamped at creation (Smart Robot `_handle_new_request`; Kefu session creation and the shell-case binding), never in the hook.
- Final-combination check is a pre-confirm validator AND re-runs right before execution (`_handle_confirm`, `confirm_kefu_turn`).
- PDF: `pickup.pdf_args(fields)` is empty for old/ordinary requests, so they take the exact old path; golden-file byte tests (1 page, 2 pages) made from the pre-feature builder.

## Things beyond or slightly different from the plan text (please check)
1. **`upsert_address` guard.** The plan listed "system-managed rows" as a read-first item. Implemented: `_valid_upsert_address_not_pickup_row` (pre-confirm) + a refusal in `handlers/uchoice/address.py` at execution. No admin-panel code touches `uchoice_address` (grep), so nothing to guard there.
2. **Modify flow / batch completion.** There is no separate "modify" service; corrections are continuations, covered by the hook. Batch completion only reads `original_fields`, so it needed only the label change (`completion_batch.load_target`).
3. **Retention warning line** (`⚠️ 仓库自提留存：...`) was not in the approved mock-up (which showed the customer line). Added for symmetry in the single completion confirmation only.
4. **Vocabulary regex** is deliberately small (`core/pickup._VOCAB`); it only triggers a question, never a classification. Negation list: 不是/不用/不要/无需/无须/非/不. Address-name spans from this turn's candidate list are removed first.
5. **Ask text** is one constant (`pickup.ASK_WHICH`) used for every disagreement outcome.
6. **`.gitattributes`** (new): golden PDFs are `binary`. **`requirements-dev.txt`**: added `pymupdf` so the PDF layout tests run (they `importorskip` it; the byte-identity tests do not need it).
7. **Golden files are tied to reportlab 5.1.0.** `requirements.txt` does not pin reportlab (an existing open follow-up). A reportlab upgrade could change bytes and fail the golden tests, which is the same signal Kefu's deferred-delivery hash check would give.

## Not verified (needs a disposable PostgreSQL)
- **V42 has not been run against any database.** The SQL was written by hand; it uses `DO $$` blocks, partial unique indexes and an `ON CONFLICT` seed. It must be run on a disposable DB (fresh and from V41) before prod.
- The 389 `postgres`-marked tests (all of `tests/kefu_integration/` and the listed files in `tests/conftest.py`) were not run. The 1228 offline tests pass.
- No live LLM check: the prompt wording and the AI's `pickup_type` classification are untested against a real model.

## Offline test inventory (new)
`tests/core/test_pickup.py` (67), `test_pickup_prompt.py` (10), `test_pickup_display.py` (15), `tests/uchoice_lifecycle/test_pickup_smart_robot.py` (16), `tests/kefu/test_pickup_kefu.py` (14), `tests/uchoice_outbound_pdf/test_pdf_pickup_mark.py` (9).

## Integration with `main` (merge, 2026-11-20)
`main` had moved 26 commits (v1.10.x - v1.12.1) while this branch was in review, so a plain merge was not possible. What changed because of it:
1. **Migration renumbered V42 -> V46.** Main already uses V42 (receiver contact/phone), V43 (rename upsert_address -> add_address), V44, V45 (update_address). `V46__customer_pickup.sql` now starts with `\encoding UTF8` like the others. Main's dedupe guard is application-level (no DB unique constraint), so the seeded `客户自提` row (same empty address/warehouse as `散客`) does not collide with it.
2. **Version 1.13.0** (main's changelog already holds 1.11.0 - 1.12.1 and an `[Unreleased]` block, which stays above my entry).
3. **System-managed rows moved to `update_address`.** `upsert_address` no longer exists. The guard is now `_valid_update_address_not_pickup_row` (first in the `update_address` chain; loads the target through `address_update.load_target` like main's own validator), a refusal in `UpdateAddressHandler`, and `exclude_pickup=True` on `update_address`'s `address_book` candidates. `add_address` is create-only, so it cannot overwrite a row (asserted by a test).
4. **PDF.** Main had already fixed the Helvetica consignee problem (`_draw_wrapped_top` falls back to the CJK font and wraps per character), so my own CJK consignee code was removed; a customer pickup now just appends `客户自提：<name>` to main's consignee text. The REMARK bar is unchanged. The golden files were regenerated from main's builder (with sender and receiver contact/phone) and the merged builder was checked to be byte-identical to main's for non-pickup requests. My changelog "Fixed" entry was dropped as obsolete.
5. Merge conflicts were in: CHANGELOG, main.py, ai/prompt_builder.py, core/kefu_turn_apply.py (kept my pre-execution check AND main's two new confirm blocks), core/pre_confirm_validators.py, core/session_manager.py, core/uchoice_delivery_order.py, core/workflow_engine.py, handlers/uchoice/address.py, handlers/uchoice/pdf_stub.py.
6. A main test (`tests/core/test_add_address_rename.py`) forbids the old service name in live source; one of my comments tripped it and was reworded.
Offline suite on the merged tree: 1348 passed, 3 skipped (391 PostgreSQL tests not run; V46 still not run against a database).
