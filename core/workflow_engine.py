from uuid import UUID
from sqlalchemy.orm import Session as DBSession

from ai.base import AIResponse
from core import session_manager, request_logger, pre_confirm_validators
from core.confirmation import build_confirmation_message, build_display_name, build_confirmation_sections
from clients.wechat_client import send_message as _send_raw


def send_message(context: dict, content: str) -> None:
    """
    Stores reply in context["_reply"] for synchronous return,
    AND calls the external API if response_url is available (for async use).
    """
    context["_reply"] = content
    # also try response_url if available (for workflow steps that run after label creation)
    response_url = context.get("response_url", "")
    if response_url:
        try:
            _send_raw(
                wechat_openid=context["wechat_openid"],
                content=content,
                response_url=response_url
            )
        except Exception as e:
            print(f"[workflow] response_url send failed: {e}", flush=True)
from handlers.registry import HANDLER_REGISTRY
from models.workflow import WorkflowStep
from models.service import ServiceType


def _session_provenance_kwargs(context: dict) -> dict:
    """
    kefu-migration-plan.md Sec 2.4 / Codex round-90 finding 4: explicit at
    session-creation time -- context["source_channel"]/["submitted_by_staff_id"]
    come from session_manager.build_context() (from AccessResult), defaulting
    to Smart Robot's existing shape when absent (offline tests build context
    dicts by hand without these keys).
    """
    staff_id = context.get("submitted_by_staff_id")
    return {
        "source_channel": context.get("source_channel") or "smart_robot",
        "opened_by_staff_id": UUID(staff_id) if staff_id else None,
    }


def _log_provenance_kwargs(context: dict) -> dict:
    staff_id = context.get("submitted_by_staff_id")
    return {
        "source_channel": context.get("source_channel") or "smart_robot",
        "submitted_by_staff_id": UUID(staff_id) if staff_id else None,
    }


def run_and_get_reply(context: dict, ai_response: AIResponse, db: DBSession) -> str:
    """
    Main orchestrator — synchronous version.
    Processes the message and returns the reply string directly
    instead of calling send_message(). The caller sends the reply
    as the encrypted webhook response.
    """
    intent = ai_response.intent
    context["_reply"] = ""  # handlers write reply here

    if _maybe_pivot_to_add_address(context, ai_response, db):
        return context.get("_reply", "")

    if intent == "new_request":
        _handle_new_request(context, ai_response, db)
    elif intent == "continuation":
        _handle_continuation(context, ai_response, db)
    elif intent == "confirm":
        _handle_confirm(context, db)
    elif intent == "cancel":
        _handle_cancel(context, db)
    elif intent == "check_services":
        _handle_check_services(context, ai_response)
    else:
        _handle_unrecognized(context, ai_response)

    return context.get("_reply", "")


def run(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """Legacy sync wrapper — kept for compatibility."""
    run_and_get_reply(context, ai_response, db)


def _maybe_pivot_to_add_address(context: dict, ai_response: AIResponse, db: DBSession) -> bool:
    """
    uchoice_outbound_request only: the AI sets ai_response.unmatched_new_address
    when the customer described a delivery destination that matched nothing
    in the injected address candidate list — a genuinely different situation
    from "customer hasn't mentioned a destination yet" (which is just a
    normal missing-field wait and never sets this).

    Rather than blocking and making the customer explicitly say "取消" before
    an address can be added, silently abandon the in-progress outbound
    session (closing its own request_log too, since uchoice_outbound_request
    isn't targets_existing_request and owns one) and open a fresh
    upsert_address session pre-seeded with the AI's best-effort company_name/
    addr guess from the same message. Nothing about the abandoned outbound
    request survives this — the customer has to resubmit it once the address
    exists (accepted tradeoff, see docs/uchoice-design.md).

    Returns True if it performed the pivot (caller should stop processing
    this turn normally — the reply is already written), False otherwise.
    """
    guess = ai_response.unmatched_new_address
    if not guess:
        return False

    session = _get_session(context, db)
    current_service_name = ai_response.service_type_name
    if session is not None and session.service_type_id:
        svc = _find_service_by_type_id(context, session.service_type_id)
        current_service_name = svc["name"] if svc else current_service_name
    if current_service_name != "uchoice_outbound_request":
        return False

    add_address_service = _find_service(context, "upsert_address")
    if add_address_service is None:
        # this role can't add addresses — fall through to normal processing;
        # destination_address_id was never set per the prompt rule, so the
        # AI's own reply (asking the customer to have an admin add it)
        # still gets sent as a normal "still collecting fields" turn.
        return False

    if session is not None:
        if session.request_log_id:
            request_logger.mark_cancelled(db, session.request_log_id)
        session_manager.close_session(db, session, status="cancelled")

    new_session = session_manager.create_session(
        db,
        wechat_openid=context["wechat_openid"],
        group_id=UUID(context["group_id"]),
        initial_message=context["content"],
        service_type_id=UUID(add_address_service["service_type_id"]),
        **_session_provenance_kwargs(context),
    )
    log = request_logger.create_log(
        db,
        wechat_openid=context["wechat_openid"],
        group_id=UUID(context["group_id"]),
        service_type_id=UUID(add_address_service["service_type_id"]),
        raw_message=context["content"],
        wechat_msg_id=context["msg_id"],
        **_log_provenance_kwargs(context),
    )
    new_session.request_log_id = log.log_id
    context["serial_number"] = log.serial_number
    db.commit()

    seed_fields = {k: v for k, v in guess.items() if v and k in ("company_name", "addr")}
    if seed_fields:
        session_manager.update_collected_fields(db, new_session, seed_fields)

    context["session_id"] = str(new_session.session_id)
    context["service_type_id"] = add_address_service["service_type_id"]
    context["collected_fields"] = new_session.collected_fields

    session_manager.add_message(db, new_session, "assistant", ai_response.reply)
    send_message(context, ai_response.reply)
    return True


# ── Intent handlers ───────────────────────────────────────────────────────────

def _handle_new_request(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is starting a new service request.
    Reject if a session is already in progress; otherwise create one.
    """
    if context.get("session_id"):
        if not _supersede_stale_target_session(context, db):
            # Session already open, and it's not a supersedable stale
            # targets_existing_request session. The prompt already asserts
            # (ai/prompt_builder.py) that a message arriving mid-session is
            # "almost certainly" an answer to the last question, not a fresh
            # request — but that classification has proven unreliable live:
            # a customer's answer to a color-clarification question got read
            # as intent=new_request because its wording didn't echo the
            # pending question, which used to hard-block with "你有一个未完成
            # 的申请" instead of just continuing the session. Rather than
            # reject the user for an AI misclassification, deterministically
            # route this turn through the existing session as a continuation
            # — same pattern as the other AI-intent overrides in this file
            # (_outbound_required_fields_present, _autoresolve_single_candidate).
            _handle_continuation(context, ai_response, db)
            return

    # find the matching service in the group's allowed list
    service = _find_service(context, ai_response.service_type_name)
    if service is None:
        if ai_response.service_type_name:
            # the AI named a real service, it's just not granted to this
            # group/role — "unsupported" is accurate feedback here.
            send_message(context, "抱歉，您的群组暂不支持该服务。如有疑问请联系管理员。")
        else:
            # service_type_name came back empty/null — the AI couldn't tell
            # what was being asked, often because a message landed as a
            # dangling fragment right after a previous session auto-closed
            # (e.g. the new stock backstop cancelling a request). "抱歉，
            # 您的群组暂不支持该服务" would wrongly imply something
            # structural/permanent; the real ask is just "please restate."
            send_message(context, "抱歉，没能理解您需要哪项服务，请重新描述一下您的需求。")
        return

    # create session
    session = session_manager.create_session(
        db,
        wechat_openid=context["wechat_openid"],
        group_id=UUID(context["group_id"]),
        initial_message=context["content"],
        service_type_id=UUID(service["service_type_id"]),
        **_session_provenance_kwargs(context),
    )

    # update context with new session id + service type so downstream (esp.
    # reply_wechat's service-specific title/sections dispatch) can use them —
    # build_context() only had session=None to work with, so service_type_id
    # was never set there.
    context["session_id"] = str(session.session_id)
    context["service_type_id"] = service["service_type_id"]

    # Log every resolved request immediately, regardless of eventual outcome —
    # EXCEPT for targets_existing_request services, which never own a log of
    # their own; they update the log they end up referencing instead.
    if not service.get("targets_existing_request", False):
        log = request_logger.create_log(
            db,
            wechat_openid=context["wechat_openid"],
            group_id=UUID(context["group_id"]),
            service_type_id=UUID(service["service_type_id"]),
            raw_message=context["content"],
            wechat_msg_id=context["msg_id"],
            **_log_provenance_kwargs(context),
        )
        session.request_log_id = log.log_id
        # kefu-migration-plan.md Sec 2.4: the one durable link
        # get_original_fields() now uses to find this request's own
        # originating session -- channel-agnostic, set once, here, at
        # creation, never inferred from actor identity afterward.
        log.origin_session_id = session.session_id
        context["serial_number"] = log.serial_number
        db.commit()

    # save any extracted fields from the first message
    if ai_response.extracted_fields:
        extracted = _sanitize_extracted_fields_before_persistence(service["name"], ai_response.extracted_fields, db, context.get("group_id"))
        session_manager.update_collected_fields(db, session, extracted)

    # context["collected_fields"] was set from session.collected_fields at
    # build_context() time, when session was still None (still {}).
    # update_collected_fields() above reassigns session.collected_fields to
    # a brand-new dict rather than mutating in place, so context's reference
    # goes stale immediately — and workflow-step handlers only ever see
    # context, never session directly, so an immediate-execution service
    # resolving all fields on this very first message would otherwise run
    # with an empty collected_fields. Must refresh before anything downstream
    # reads it.
    context["collected_fields"] = session.collected_fields

    auto_resolved = _autoresolve_single_candidate(context, service, session, db)

    # Ready once every declared required field is actually present post-
    # sanitization (_all_required_fields_present), or a deterministic
    # override applies -- never on the AI's own pre-sanitization claim (see
    # that function's docstring for why).
    if _all_required_fields_present(service, session.collected_fields) or auto_resolved or _outbound_required_fields_present(service, session):
        _on_all_fields_collected(context, ai_response, service, session, db)
    else:
        session_manager.add_message(db, session, "assistant", ai_response.reply)
        send_message(context, ai_response.reply)
        _close_if_no_pending_candidates(service, session, context, db)


def _supersede_stale_target_session(context: dict, db: DBSession) -> bool:
    """
    Scope-by-target for targets_existing_request sessions (confirm_inbound_
    completion/confirm_outbound_completion): these track progress toward
    confirming one specific OTHER request, not a payload of their own, so an
    open one for REQ-62 should never block starting a fresh completion for
    REQ-63 — unlike a regular multi-turn submission (e.g.
    uchoice_outbound_request mid-collection), which genuinely can't have two
    in flight without corrupting collected_fields, so that case still hard-
    blocks. Nothing is lost by closing the stale one here: nothing commits to
    real data until the final confirm, so the user can always re-trigger it
    later (same "safe to abandon and retry" property already relied on
    elsewhere in this flow). Returns True if it superseded (caller should
    proceed with the new request), False if the block should stand.
    """
    old_session = _get_session(context, db)
    if old_session is None:
        return False
    old_service = _find_service_by_type_id(context, old_session.service_type_id)
    if not old_service or not old_service.get("targets_existing_request", False):
        return False
    session_manager.close_session(db, old_session, status="cancelled")
    context["session_id"] = None
    return True


_REFERENCE_SERIAL_CANDIDATE_KEYS = {
    "confirm_inbound_completion":  "pending_inbound_requests",
    "confirm_outbound_completion": "pending_outbound_requests",
}


def _autoresolve_single_candidate(context: dict, service: dict, session, db: DBSession) -> bool:
    """
    Deterministic replacement for relying on the AI to notice "only one
    pending candidate, don't ask which one" — that instruction has proven
    flaky under live testing even with worked examples reinforcing it.
    reference_serial is the ONLY required field on both targets_existing_
    request services, so resolving it here means all required fields are
    now genuinely collected — no AI judgment needed for this decision at
    all, and it doesn't depend on the AI having reasoned correctly about
    which service is even active this turn.
    """
    if not service.get("targets_existing_request", False):
        return False
    if session.collected_fields.get("reference_serial"):
        return False
    candidate_key = _REFERENCE_SERIAL_CANDIDATE_KEYS.get(service["name"])
    if not candidate_key:
        return False
    candidates = (context.get("uchoice_candidates") or {}).get(candidate_key) or []
    if len(candidates) != 1:
        return False
    session_manager.update_collected_fields(db, session, {"reference_serial": candidates[0]["serial_number"]})
    return True


# Moved to core/uchoice_field_sanitization.py (signed cross-review plan,
# Section C4) so core/kefu_turn_apply.py no longer privately reaches into
# this module for it. Imported (not re-defined) so every existing
# monkeypatch target of the form `workflow_engine._sanitize_..._persistence`
# keeps resolving correctly, and so the bare calls below still pick up a
# patched version via this module's own namespace.
from core.uchoice_field_sanitization import (
    _SKU_LINES_FIELD_BY_SERVICE,
    _sanitize_role_change_fields_before_persistence,
    sanitize_extracted_fields_before_persistence as _sanitize_extracted_fields_before_persistence,
)


def _all_required_fields_present(service: dict, collected_fields: dict) -> bool:
    """
    Generic post-sanitization readiness check, mirroring core/kefu_turn_apply.py's
    predicate of the same name. Smart Robot historically had no equivalent —
    every readiness decision leaned on ai_response.all_fields_collected, the
    AI's OWN claim, computed before _sanitize_extracted_fields_before_persistence
    ever ran. If the field the AI based that claim on gets silently dropped by
    sanitization (a malformed non-list sku_lines value, etc.), trusting the
    stale claim lets a turn sail past a missing-field re-prompt it should have
    hit — the same bug fixed on the Kefu side in commit 38c812a.

    Deliberately service-agnostic: an empty input_schema.required list (most
    read-only services) is ready immediately; every other service is ready
    only once every one of its declared required fields is actually present
    and non-empty in collected_fields. This is the sole general authority at
    both readiness branch points below -- the AI's all_fields_collected flag
    is no longer consulted there. Existing deterministic overrides
    (_autoresolve_single_candidate, _outbound_required_fields_present) remain
    unchanged alongside it; they cover genuine cases (a single eligible
    reference_serial candidate, uchoice_outbound_request's boxes_per_pallet
    sub-field) this generic schema check was never meant to replace.
    """
    required = (service.get("input_schema") or {}).get("required") or []
    return all(collected_fields.get(field) not in (None, "", []) for field in required)


def _outbound_required_fields_present(service: dict, session) -> bool:
    """
    Deterministic override for uchoice_outbound_request: its only genuinely
    required top-level fields are sku_lines and destination_address_id —
    boxes_per_pallet is just a sub-field inside a sku_lines entry, never
    declared in input_schema.required/optional at all, and has no
    independent completion signal of its own. The AI has repeatedly (even
    after several rounds of prompt tightening) treated a missing
    boxes_per_pallet as blocking anyway and stalled asking about it, live-
    tested at a nonzero failure rate no amount of rewording fully closed.
    Once the two real required fields are present, force progression to
    _on_all_fields_collected regardless of what the AI's own
    all_fields_collected flag says — _resolve_outbound_pallet_defaults
    (called from there) is what actually resolves or asks about
    boxes_per_pallet now, deterministically.
    """
    if service["name"] != "uchoice_outbound_request":
        return False
    fields = session.collected_fields
    sku_lines = fields.get("sku_lines")
    if not isinstance(sku_lines, list) or not sku_lines:
        return False
    # Sev 2: a non-empty list isn't enough -- every line must actually name
    # a real product. A line with a missing/blank sku_code (e.g. the AI
    # extracted a quantity but never resolved what it was for) must not
    # count as "collected," or it reaches confirmation as "?". A malformed
    # non-dict line (Codex round-28 finding 3) must be treated as
    # not-yet-collected too, not crash this check via .get() on a non-dict.
    if any(not isinstance(line, dict) or not (line.get("sku_code") or "").strip() for line in sku_lines):
        return False
    return bool(fields.get("destination_address_id"))


def _close_if_no_pending_candidates(service: dict, session, context: dict, db: DBSession) -> bool:
    """
    A targets_existing_request session that still lacks reference_serial and
    has zero pending candidates can never resolve — there's nothing to wait
    for. Leaving it open anyway meant the user's next unrelated message got
    routed as a `continuation` of this now-orphaned session instead of a
    fresh new_request. Observed live: the AI, continuing a stuck
    confirm_outbound_completion session with nothing concrete to anchor on,
    drifted into talking about confirm_inbound_completion instead — a
    confusing reply for a session that was never about inbound at all.
    Closing here means the very next message starts clean.
    """
    if not service.get("targets_existing_request", False):
        return False
    if session.collected_fields.get("reference_serial"):
        return False
    candidate_key = _REFERENCE_SERIAL_CANDIDATE_KEYS.get(service["name"])
    if not candidate_key:
        return False
    candidates = (context.get("uchoice_candidates") or {}).get(candidate_key) or []
    if candidates:
        return False
    session_manager.close_session(db, session, status="cancelled")
    return True


def _on_all_fields_collected(
    context: dict,
    ai_response: AIResponse,
    service: dict,
    session,
    db: DBSession
) -> None:
    """
    Shared branch point once every required field is genuinely present
    (per _all_required_fields_present) or a deterministic override applies
    — used by both _handle_new_request and _handle_continuation. Resolves
    a target request
    for targets_existing_request services, runs pre-confirmation validators,
    then either shows a confirmation template or executes immediately
    depending on requires_confirmation.
    """
    if service.get("targets_existing_request", False):
        target, error = _resolve_target_request(session, db)
        if error:
            # Unlike _close_if_no_pending_candidates (which only fires when
            # reference_serial is still missing), this is reached once the
            # AI already believed it had a reference_serial and it still
            # doesn't resolve to a real, matching request. Leaving the
            # session open here left it stuck 'active' indefinitely — the
            # customer's next, unrelated message then got blocked as "你有
            # 一个未完成的申请" instead of starting cleanly. Nothing commits
            # to real data on this path, so it's always safe to abandon.
            session_manager.close_session(db, session, status="cancelled")
            send_message(context, error)
            return
        session.request_log_id = target.log_id
        context["serial_number"] = target.serial_number
        db.commit()

    if service["name"] == "uchoice_outbound_request":
        _resolve_outbound_warehouse_default(context, session, db)
        clarification = _resolve_outbound_pallet_defaults(context, session, db)
        if clarification:
            send_message(context, clarification)
            return
        if _reject_invalid_outbound_stock(context, session, db):
            return

    if service["name"] == "uchoice_inbound_request":
        _resolve_inbound_warehouse_default(context, session, db)

    if service["name"] == "confirm_outbound_completion":
        _resolve_outbound_loose_pick_defaults(context, session, db)

    error = pre_confirm_validators.run(service["name"], context, session.collected_fields, db)
    if error:
        send_message(context, error)
        return

    if service.get("requires_confirmation", True):
        _trigger_confirmation(context, session, db)
    else:
        _execute_workflow_and_finish(context, session, db)
        # _execute_workflow_and_finish sends its own reply via reply_wechat / failure message


def _resolve_outbound_warehouse_default(context: dict, session, db: DBSession) -> None:
    """
    warehouse_code became optional on uchoice_outbound_request (V12) —
    real dispatch messages essentially never state it (found reviewing 57
    real outbound requests: not one names JFK or DE explicitly). Defaults to
    JFK, persisted here before confirmation is built (and before
    _resolve_outbound_pallet_defaults runs, since that function's storage-
    bucket lookup needs the real warehouse_code to find the right buckets)
    — marked with _warehouse_auto_default so the confirmation display can
    flag it as an assumption the customer can still correct.
    """
    fields = session.collected_fields
    if fields.get("warehouse_code"):
        return
    session_manager.update_collected_fields(db, session, {
        "warehouse_code": "JFK",
        "_warehouse_auto_default": True,
    })
    context["collected_fields"] = session.collected_fields


def _resolve_inbound_warehouse_default(context: dict, session, db: DBSession) -> None:
    """
    kefu-migration-plan.md Sec 3 (round 64 -- the user's final answer):
    inbound also defaults warehouse_code to JFK when unstated, same
    two-tier rule as outbound's _resolve_outbound_warehouse_default above
    (explicit when stated, JFK otherwise, no third tier). This is new
    code, not a reuse of the outbound resolver -- Codex round-62 finding
    3 confirmed the outbound one is outbound-specific by name and
    confirmation-flow position; inbound previously had no such default at
    all (warehouse_code was a hard-required schema field). The
    corresponding service_type.input_schema migration moves
    warehouse_code from required to optional to match.
    """
    fields = session.collected_fields
    if fields.get("warehouse_code"):
        return
    session_manager.update_collected_fields(db, session, {
        "warehouse_code": "JFK",
        "_warehouse_auto_default": True,
    })
    context["collected_fields"] = session.collected_fields


def _resolve_outbound_pallet_defaults(context: dict, session, db: DBSession) -> str | None:
    """
    Resolves boxes_per_pallet for every palletized outbound line entirely
    from real, current uchoice_storage state — never from whatever the AI
    put in the field. A present boxes_per_pallet is only ever treated as
    "the customer said so" if it actually matches a real bucket with enough
    pallets; anything else (missing, hallucinated, matches nothing,
    insufficient quantity) is re-derived identically, as if it had never
    been given. This is the fix for the actual recurring bug: earlier
    versions only resolved a MISSING value and blindly trusted a present
    one, which is exactly how a fabricated or self-selected-but-thin value
    kept slipping through despite repeated prompt tightening telling the AI
    not to fill this in.

    - zero real buckets for that sku+warehouse: line is left as-is:
      unresolvable here, so _reject_invalid_outbound_stock (called right
      after this returns None) cancels the whole request — no bucket size
      exists, so there's no question worth asking.
    - exactly one real bucket: unambiguous, silently auto-applied and
      persisted (marked _bpp_auto_default for the confirmation display).
    - 2+ real buckets: genuine ambiguity only a human should resolve.
      Returns a deterministic, Python-formatted clarification message
      listing every real option — never AI-authored text, so its phrasing
      can't drift or go blind the way repeated prompt attempts did.

    Returns the clarification message to send (caller should send it and
    stop processing this turn, leaving the session open) if any line needs
    one, else None (every line is now resolved or left for the stock
    backstop to reject).
    """
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    fields = session.collected_fields
    sku_lines = fields.get("sku_lines")
    if not sku_lines:
        return None

    warehouse_code = fields.get("warehouse_code")
    sku_labels = None
    changed = False
    resolved_lines = []
    clarifications = []

    for line in sku_lines:
        if "box_count" in line:
            resolved_lines.append(line)
            continue

        buckets = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=line.get("sku_code"))
            .filter(UchoiceStorage.pallet_count > 0)
            .order_by(UchoiceStorage.boxes_per_pallet.asc())
            .all()
        )

        pallet_count = line.get("pallet_count")
        stated_bpp = line.get("boxes_per_pallet")
        stated_bucket = next((b for b in buckets if b.boxes_per_pallet == stated_bpp), None) if stated_bpp is not None else None
        if stated_bucket is not None and pallet_count is not None and stated_bucket.pallet_count >= pallet_count:
            # genuinely real and sufficient — trust it as a real customer statement
            resolved_lines.append(line)
            continue

        if not buckets:
            # nothing to resolve to at all — leave untouched, the stock
            # backstop right after this function cancels the request
            resolved_lines.append(line)
            continue

        if len(buckets) == 1:
            b = buckets[0]
            resolved_lines.append({**line, "boxes_per_pallet": b.boxes_per_pallet, "_bpp_auto_default": True})
            changed = True
            continue

        if sku_labels is None:
            sku_labels = sku_label_map(db)
        label = sku_labels.get(line.get("sku_code"), line.get("sku_code"))
        options = "、".join(f"{b.boxes_per_pallet}箱/托（现有{b.pallet_count}托）" for b in buckets)
        clarifications.append(f"{label}：{options}")
        resolved_lines.append(line)

    if clarifications:
        return f"请确认托盘规格——{'；'.join(clarifications)}。请告知您要哪一种。"

    if changed:
        session_manager.update_collected_fields(db, session, {"sku_lines": resolved_lines})
        context["collected_fields"] = session.collected_fields

    return None


def _reject_invalid_outbound_stock(context: dict, session, db: DBSession) -> bool:
    """
    A palletized outbound line's boxes_per_pallet — whether stated by the
    customer or filled in by _resolve_outbound_pallet_defaults just above —
    must correspond to real, sufficient stock. There's no fallback question
    worth asking here: if the bucket doesn't exist at all (or doesn't have
    enough pallets), no answer the customer gives makes stock materialize,
    and a palletized order can't span multiple bucket sizes the way a loose
    pick can (see _resolve_outbound_loose_pick_defaults for that case).
    Cancels the request outright (session + its own request_log — this
    service isn't targets_existing_request, so it owns one) rather than
    leaving a request open that can only ever fail later, at completion
    time — the exact failure mode this replaces (a fabricated
    boxes_per_pallet baked into a 'processing' request, crashing when the
    warehouseman eventually confirmed it).

    Returns True if it rejected the request (caller should stop processing
    this turn), False if every palletized line checks out.
    """
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    fields = session.collected_fields
    warehouse_code = fields.get("warehouse_code")
    sku_lines = fields.get("sku_lines") or []

    problems = []
    for line in sku_lines:
        if "box_count" in line:
            continue  # loose lines aren't checked here
        pallet_count = line.get("pallet_count")
        bpp = line.get("boxes_per_pallet")

        if pallet_count is None:
            continue  # not yet fully specified — normal field collection handles this

        if bpp is None:
            # _resolve_outbound_pallet_defaults already tried and gave up --
            # this is the zero-real-bucket case (Sev 1), not "not yet
            # specified." No answer can materialize a bucket size that
            # doesn't exist, so this is unconditionally terminal.
            sku_exists_at_all = (
                db.query(UchoiceStorage)
                .filter_by(warehouse_code=warehouse_code, sku_code=line.get("sku_code"))
                .filter(UchoiceStorage.pallet_count > 0)
                .first()
            )
            if sku_exists_at_all is None:
                problems.append((line.get("sku_code"), "任意规格", 0, pallet_count))
            continue

        bucket = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=line.get("sku_code"), boxes_per_pallet=bpp)
            .first()
        )
        available = bucket.pallet_count if bucket else 0
        if available < pallet_count:
            problems.append((line.get("sku_code"), bpp, available, pallet_count))

    if not problems:
        return False

    sku_labels = sku_label_map(db)
    lines_text = "；".join(
        f"{sku_labels.get(sku, sku)}@{bpp}/托 现有 {available} 托，申请 {requested} 托"
        for sku, bpp, available, requested in problems
    )
    message = f"申请已取消：{warehouse_code} 仓库没有足够库存可满足此次出库——{lines_text}。请核实商品规格或数量后重新提交。"

    if session.request_log_id:
        request_logger.mark_cancelled(db, session.request_log_id)
    session_manager.close_session(db, session, status="cancelled")
    send_message(context, message)
    return True


def _resolve_outbound_loose_pick_defaults(context: dict, session, db: DBSession) -> None:
    """
    A loose (box_count) original outbound line has no default bucket at
    request time — nobody yet knows which physical pallet the warehouseman
    will draw from. At completion time, if the warehouseman didn't say which
    bucket(s) to pick from, default to consuming the smallest-boxes_per_pallet
    buckets first (use up small/odd pallets before opening a bigger one),
    spilling into the next-smallest bucket if one alone doesn't cover the
    amount. Persisted here, before confirmation is built, mirroring
    _resolve_outbound_pallet_defaults's persist-before-display pattern —
    marked with _auto_default on each pick so the confirmation display can
    flag it as an assumption the warehouseman can still correct.
    """
    from core.uchoice_context import resolve_completion_target, resolve_loose_pick_defaults

    reference_serial = session.collected_fields.get("reference_serial")
    if not reference_serial:
        return
    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        return

    loose_box_counts = {
        l["sku_code"]: l["box_count"]
        for l in (original_fields.get("sku_lines") or []) if "box_count" in l
    }
    if not loose_box_counts:
        return

    warehouse_code = original_fields.get("warehouse_code")
    fulfillment_lines = session.collected_fields.get("fulfillment_lines") or []
    by_sku = {l.get("sku_code"): l for l in fulfillment_lines}
    resolved_lines = list(fulfillment_lines)
    changed = False

    for sku, box_count_needed in loose_box_counts.items():
        existing = by_sku.get(sku)
        if existing and existing.get("picks"):
            continue  # already explicitly specified this turn
        picks = resolve_loose_pick_defaults(db, warehouse_code, sku, box_count_needed)
        if picks is None:
            continue  # insufficient stock — leave unresolved, pre_confirm_validators will block with a clear message
        new_line = {"sku_code": sku, "picks": [{**p, "_auto_default": True} for p in picks]}
        if existing:
            resolved_lines = [new_line if l.get("sku_code") == sku else l for l in resolved_lines]
        else:
            resolved_lines.append(new_line)
        changed = True

    if changed:
        session_manager.update_collected_fields(db, session, {"fulfillment_lines": resolved_lines})
        context["collected_fields"] = session.collected_fields


def _resolve_target_request(session, db: DBSession):
    """
    For targets_existing_request services — resolves session.collected_fields
    ["reference_serial"] (already disambiguated by the AI against the injected
    candidate list) to an existing RequestLog. Returns (log, None) on success,
    (None, error_message) otherwise. Deeper validation (warehouse match,
    direction) happens in the service's own lookup_and_validate handler step,
    right before the mutation it protects.
    """
    from models.request_log import RequestLog

    reference_serial = session.collected_fields.get("reference_serial")
    if not reference_serial:
        return None, "未能确定要处理的申请编号，请重新描述或提供申请编号。"

    target = db.query(RequestLog).filter_by(serial_number=reference_serial).first()
    if target is None:
        return None, f"未找到申请编号 {reference_serial}，请确认后重试。"
    if target.status != "processing":
        return None, f"申请 {reference_serial} 当前状态为「{target.status}」，无法处理。"

    return target, None


def _trigger_confirmation(context: dict, session, db: DBSession) -> None:
    """
    Builds the confirmation template and moves the session to
    pending_confirmation. request_log already exists at this point (created
    in _handle_new_request, or resolved to a target in _on_all_fields_collected)
    — this function only renders and sends the template.
    """
    # context["serial_number"] is only populated within the turn the log was
    # created/resolved — on a later continuation turn it's a fresh context,
    # so fall back to the DB via session.request_log_id (same pattern as the
    # Q1 fix in _handle_confirm).
    if not context.get("serial_number") and session.request_log_id:
        from models.request_log import RequestLog
        log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
        if log:
            context["serial_number"] = log.serial_number

    service_type = db.query(ServiceType).filter_by(
        service_type_id=session.service_type_id
    ).first()
    note = service_type.confirmation_note if service_type else None
    service_type_name = service_type.name if service_type else ""

    display_name = build_display_name(service_type_name, session.collected_fields)
    sections = build_confirmation_sections(service_type_name, session.collected_fields, db)

    confirmation_text = build_confirmation_message(
        serial_number=context.get("serial_number", ""),
        service_display_name=display_name,
        sections=sections,
        note=note
    )

    session_manager.add_message(db, session, "assistant", confirmation_text)
    session.status = "pending_confirmation"
    db.commit()

    send_message(context, confirmation_text)


def _handle_continuation(context: dict, ai_response: AIResponse, db: DBSession) -> None:
    """
    User is providing more information for an existing session.
    Updates collected fields. Triggers confirmation when all required fields collected.
    """
    session = _get_session(context, db)
    if session is None:
        send_message(context, "抱歉，未找到您的申请，请重新发起。")
        return

    service = _find_service_by_type_id(context, session.service_type_id)

    session_manager.add_message(db, session, "user", context["content"])
    extracted = ai_response.extracted_fields
    if service is not None:
        extracted = _sanitize_extracted_fields_before_persistence(service["name"], extracted, db, context.get("group_id"))
    session_manager.update_collected_fields(db, session, extracted)

    # see the matching comment in _handle_new_request — same staleness bug,
    # this is the path that actually surfaced it live (the last required
    # field, e.g. warehouse_code, supplied on a later turn ended up missing
    # from the executed query because context still held the pre-merge dict).
    context["collected_fields"] = session.collected_fields

    auto_resolved = service is not None and _autoresolve_single_candidate(context, service, session, db)
    force_complete = service is not None and _outbound_required_fields_present(service, session)
    # Ready once every declared required field is actually present post-
    # sanitization -- never on the AI's own pre-sanitization claim (see
    # _all_required_fields_present's docstring for why).
    schema_ready = service is not None and _all_required_fields_present(service, session.collected_fields)

    if (schema_ready or auto_resolved or force_complete) and service is not None:
        _on_all_fields_collected(context, ai_response, service, session, db)
    else:
        session_manager.add_message(db, session, "assistant", ai_response.reply)
        send_message(context, ai_response.reply)
        if service is not None:
            _close_if_no_pending_candidates(service, session, context, db)


def _handle_confirm(context: dict, db: DBSession) -> None:
    """
    User confirmed the summary. Run all workflow steps in order.
    On success: complete session and request_log.
    On failure: mark both failed and notify user.
    """
    session = _get_session(context, db)
    if session is None or session.status != "pending_confirmation":
        send_message(context, "抱歉，未找到待确认的申请，请重新发起。")
        return

    # Q1 fix: serial_number is None in context when confirm message arrives
    # because it was set in the previous request's context but not persisted.
    # Load it from the linked request_log.
    if not context.get("serial_number") and session.request_log_id:
        from models.request_log import RequestLog
        log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
        if log:
            context["serial_number"] = log.serial_number

    _execute_workflow_and_finish(context, session, db)


def _execute_workflow_and_finish(context: dict, session, db: DBSession) -> None:
    """
    Shared by _handle_confirm and the requires_confirmation=false immediate
    path. Transitions the log to 'processing', runs workflow steps, marks
    success/failure, closes the session.

    awaits_completion services (uchoice_inbound_request/uchoice_outbound_request)
    are two-step: confirming only starts the request — it isn't actually done
    until a warehouseman later runs the matching targets_existing_request
    completion service against it. For those, a successful run leaves the log
    at 'processing' (only the session closes as completed); mark_success is
    skipped here and happens later, on the target log, when that completion
    service's own _execute_workflow_and_finish runs.
    """
    # context["serial_number"] may not have been set yet on this call path —
    # e.g. a requires_confirmation=false service executed straight from
    # _handle_continuation, which never sets it (unlike _handle_new_request/
    # _trigger_confirmation/_handle_confirm). Without this, reply_wechat.py's
    # `context.get("serial_number", "")` returns the existing None value
    # (the key is present, just unset) and prints the literal string "None".
    if not context.get("serial_number") and session.request_log_id:
        from models.request_log import RequestLog
        log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
        if log:
            context["serial_number"] = log.serial_number

    if session.request_log_id:
        request_logger.mark_processing(db, session.request_log_id)

    service_for_split_check = _find_service_by_type_id(context, session.service_type_id)
    service_name_for_split_check = service_for_split_check["name"] if service_for_split_check else None

    # Phase 2 atomicity fix (systemic-validation-addendum.md Sec 3b) only
    # applies to the named U-Choice services it actually covers. Every other
    # service -- FedEx, UPS, OMS, upsert_address, anything else -- runs
    # through the single-phase path below unchanged, exactly as before
    # Phase 2. Deliberately an explicit service-name allowlist, not
    # step-type inference: the first version of this fix inferred
    # eligibility from step *types* present in the workflow (Codex round-28
    # finding 2 -- create_fedex_label/create_ups_label/oms_create_workorder
    # got misclassified as side effects that way, marking FedEx/UPS/OMS
    # requests successful before the label/work order ever ran). An
    # allowlist by service name can't accidentally net an unrelated service
    # the same way a step-type-presence check can. Extended (Codex round 30)
    # to adjust_storage/move_storage/recount_storage, which the signed
    # addendum's Sec 3b/exposure table also names but the original
    # step-type-based check missed entirely (their workflows are just
    # {*_storage_txn -> reply_wechat}, with no generate_pdf_stub/
    # complete_existing_request step to trigger on) -- without this, a
    # reply_wechat failure for these three services would roll back an
    # already-valid, already-computed inventory change purely because the
    # final WeChat message failed to send.
    _UCHOICE_SPLIT_ELIGIBLE_SERVICES = {
        "uchoice_outbound_request",
        "uchoice_inbound_request",
        "confirm_inbound_completion",
        "confirm_outbound_completion",
        "adjust_storage",
        "move_storage",
        "recount_storage",
    }
    uses_uchoice_split = service_name_for_split_check in _UCHOICE_SPLIT_ELIGIBLE_SERVICES

    if not uses_uchoice_split:
        try:
            _run_workflow_steps(context, session, db, phase="all")
            service = _find_service_by_type_id(context, session.service_type_id)
            awaits_completion = bool(service.get("awaits_completion", False)) if service else False
            if not awaits_completion:
                request_logger.mark_success(db, session.request_log_id, context.get("result", {}), commit=False)
            session_manager.close_session(db, session, status="completed", commit=False)
            db.commit()
        except Exception as e:
            import traceback
            print(f"[workflow] STEP FAILED: {e}", flush=True)
            traceback.print_exc()
            db.rollback()
            request_logger.mark_failed(db, session.request_log_id, error_detail=str(e))
            session_manager.close_session(db, session, status="failed")
            send_message(context, "申请处理失败，请稍后重试或联系管理员。")
        return

    # DB-only steps (storage deltas, business-state transitions) commit as
    # one real transaction, together with the success/failure status change
    # and the session close -- commit=False on every call below defers all
    # of them to the single db.commit() (or db.rollback()) at the end of
    # this block, closing Codex round-28 finding 1 (mark_success/
    # close_session used to commit independently, so a failure between them
    # could leave storage changes durable but the session/log inconsistent).
    try:
        _run_workflow_steps(context, session, db, phase="db")
        service = _find_service_by_type_id(context, session.service_type_id)
        awaits_completion = bool(service.get("awaits_completion", False)) if service else False
        if not awaits_completion:
            request_logger.mark_success(db, session.request_log_id, context.get("result", {}), commit=False)
        session_manager.close_session(db, session, status="completed", commit=False)
        db.commit()

    except Exception as e:
        import traceback
        print(f"[workflow] STEP FAILED (db phase): {e}", flush=True)
        traceback.print_exc()
        db.rollback()
        request_logger.mark_failed(db, session.request_log_id, error_detail=str(e))
        session_manager.close_session(db, session, status="failed")
        send_message(context, "申请处理失败，请稍后重试或联系管理员。")
        return

    try:
        _run_workflow_steps(context, session, db, phase="side_effect")
        db.commit()
    except Exception as e:
        import traceback
        print(f"[workflow] STEP FAILED (post-commit side effect, inventory/business state already committed): {e}", flush=True)
        traceback.print_exc()
        db.rollback()
        # Deliberately NOT mark_failed / NOT close_session(status="failed") --
        # the operation itself already succeeded and committed. A PDF/webhook/
        # reply failure here is a delivery problem, not an operation failure.


def _handle_cancel(context: dict, db: DBSession) -> None:
    """
    User explicitly cancelled. Close the session and notify.
    Only marks request_log as cancelled if this session actually owns the
    log (i.e. its service isn't targets_existing_request) — cancelling a
    completion-confirmation session must never touch the original request
    it was merely referencing. The same owns_log distinction gates whether
    the cancellation message names a serial number: a targets_existing_
    request session's log is the ORIGINAL request it references, not one
    this session owns, so naming it here would falsely imply that request
    itself was cancelled (matches Kefu's ConfirmationCancelledOutcome).
    """
    session = _get_session(context, db)
    serial_number = None
    if session:
        service = _find_service_by_type_id(context, session.service_type_id)
        owns_log = service is None or not service.get("targets_existing_request", False)
        if session.request_log_id and owns_log:
            request_logger.mark_cancelled(db, session.request_log_id)
            from models.request_log import RequestLog
            log = db.query(RequestLog).filter_by(log_id=session.request_log_id).first()
            if log:
                serial_number = log.serial_number
        session_manager.close_session(db, session, status="cancelled")
    if serial_number:
        send_message(context, f"已取消（{serial_number}），您可以随时发起新申请。")
    else:
        send_message(context, "已取消，您可以随时发起新申请。")


def _handle_check_services(context: dict, ai_response: AIResponse) -> None:
    """AI already listed available services in its reply. Just send it."""
    send_message(context, ai_response.reply)


def _handle_unrecognized(context: dict, ai_response: AIResponse) -> None:
    """
    Message couldn't be classified. Send the AI's reply.
    Existing session stays open — user can continue or cancel.
    """
    send_message(context, ai_response.reply)


# ── Workflow step runner ──────────────────────────────────────────────────────

# Step types the Phase 2 DB-phase/post-commit-side-effect split actually
# targets (systemic-validation-addendum.md Sec 3b, Phase 3's PDF timing).
# Deliberately narrow: create_fedex_label/create_ups_label/oms_create_workorder
# are NOT here even though they're real external calls too, because for
# those services the label/work order IS the required operational work, not
# a best-effort delivery-notification concern -- treating them as
# non-fatal "side effects" would mark a request successful before the label
# was ever created (Codex round-28 finding 2, a real regression the first
# version of this fix introduced). _execute_workflow_and_finish only invokes
# this split at all for workflows that contain one of these two step types;
# every other workflow (FedEx, UPS, OMS, everything else) runs unaffected
# through the single-phase path, unchanged from before Phase 2.
_SIDE_EFFECT_STEP_TYPES = {
    "generate_pdf_stub",
    "complete_existing_request",   # cross-group webhook
    "reply_wechat",                # final WeChat send
}


def _run_workflow_steps(context: dict, session, db: DBSession, phase: str) -> None:
    """
    Loads and executes this workflow's steps of one phase, in step_order.
    Each step handler receives the full context dict and its step config.
    Results are accumulated in context["result"] for subsequent steps to read.

    phase="all": every step, in order -- the original, single-phase
    behavior, used for every workflow the Sec 3b split doesn't apply to.
    phase="db": every step NOT in _SIDE_EFFECT_STEP_TYPES -- pure DB reads/
    writes, safe to run inside one transaction with row locks held.
    phase="side_effect": only steps in _SIDE_EFFECT_STEP_TYPES -- run after
    the DB phase's transaction has already committed, per Sec 3b's rule that
    a delivery failure here must never roll back or relabel already-committed
    inventory/business state.

    Called twice per confirm (once per phase, "db" then "side_effect") by
    _execute_workflow_and_finish for workflows the split applies to, sharing
    context["result"] across both calls -- the "db"/"all" call resetting it
    once per operation is what the first call owns.
    """
    workflow_id = _get_workflow_id(context, session)
    if workflow_id is None:
        raise RuntimeError("No workflow found for this session's service type.")

    all_steps = (
        db.query(WorkflowStep)
        .filter_by(workflow_id=workflow_id)
        .order_by(WorkflowStep.step_order)
        .all()
    )
    if phase == "all":
        steps = all_steps
        context["result"] = {}
        context["request_log_id"] = str(session.request_log_id) if session.request_log_id else None
    elif phase == "db":
        steps = [s for s in all_steps if s.step_type not in _SIDE_EFFECT_STEP_TYPES]
        context["result"] = {}
        context["request_log_id"] = str(session.request_log_id) if session.request_log_id else None
    elif phase == "side_effect":
        steps = [s for s in all_steps if s.step_type in _SIDE_EFFECT_STEP_TYPES]
    else:
        raise ValueError(f"unknown phase: {phase!r}")

    # load group-level config for this service (ydd_cust_id, ydd_channel_id, etc.)
    group_config = _get_group_config(context, session)

    for step in steps:
        handler_class = HANDLER_REGISTRY.get(step.step_type)
        if handler_class is None:
            raise RuntimeError(f"No handler registered for step_type: '{step.step_type}'")

        # merge step-level config with group-level config.
        # group_config takes precedence — it carries credentials specific to this group.
        merged_config = {**step.config, **group_config}

        handler = handler_class()
        step_result = handler.handle(context, merged_config, db)
        context["result"].update(step_result)


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_session(context: dict, db: DBSession):
    """Reload the current session from DB using session_id in context."""
    from models.session import ConversationSession
    session_id = context.get("session_id")
    if not session_id:
        return None
    return db.query(ConversationSession).filter_by(session_id=session_id).first()


def _find_service(context: dict, service_type_name: str | None) -> dict | None:
    """
    Finds a service entry in the group's allowed_services list by name.
    Returns the dict (with service_type_id and workflow_id) or None.
    """
    if not service_type_name:
        return None
    for service in context.get("allowed_services", []):
        if service["name"] == service_type_name:
            return service
    return None


def _find_service_by_type_id(context: dict, service_type_id) -> dict | None:
    """Finds a service entry in allowed_services by service_type_id (UUID or str)."""
    if service_type_id is None:
        return None
    target = str(service_type_id)
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == target:
            return service
    return None


def _get_workflow_id(context: dict, session) -> UUID | None:
    """
    Finds the workflow_id for the session's service type
    from the context's allowed_services list.
    """
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == str(session.service_type_id):
            return UUID(service["workflow_id"])
    return None


def _get_group_config(context: dict, session) -> dict:
    """
    Returns the group-specific config for the session's service type.
    This contains credentials like ydd_cust_id, ydd_channel_id.
    Merged with step.config before passing to each handler.
    """
    for service in context.get("allowed_services", []):
        if service["service_type_id"] == str(session.service_type_id):
            return service.get("group_config", {})
    return {}
