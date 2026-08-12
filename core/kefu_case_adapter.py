"""
Kefu case-turn adapter -- implements core.kefu_contracts.CaseTurnProcessor,
bridging Codex's transport (core/kefu_sync.py's run_worker_once) to
Claude's owned session/case pipeline.

Codex round-94: for the currently-enabled Kefu allowlist, this
module does NOT call core/workflow_engine.py at all -- that engine's
helpers (session_manager.create_session, request_logger.create_log,
etc.) each commit independently, which is correct for Smart Robot but
cannot give a Kefu turn a genuine atomic boundary. Business-state
application for the allowlist goes through core/kefu_turn_apply.py
instead, which builds every object via db.add()/db.flush() and never
commits -- this module (_process_turn) commits exactly once per turn,
bundling the business state, the case_execution ledger transition, the
msgid-bearing case_turn audit row, the staff binding, and the durable
delivery enqueue together. A crash anywhere before that single commit
leaves nothing durable at all (the execution ledger row stays 'claimed',
safely re-driven from scratch on retry) -- no more partial states to
reason about for this path. The customer-scoped inbound, outbound, and
address services now add a native pending-confirmation state machine and
logical confirmation claim; unrelated mutations remain gated.

Built per docs/ai-collaboration/discussion.md round 88's four integration
requirements: registration intake (core/kefu_registration.py), channel-
neutral final rendering (handlers/reply_wechat.py's _reply fix),
Kefu-only startup (config.py's feature-flag fix), completion-notice
audience tracking (core/kefu_completion_notice.py).

A real case reply is enqueued durably via core/kefu_delivery.enqueue_text
(session-scoped), not sent directly -- core/kefu_delivery_worker.py's
scheduled job (wired in main.py) is what actually calls deliver_one()
against each pending row, which is what gives a reply Sec 11.3's
closed-window deferral/retry/quota handling instead of being silently
lost if the 48h window happens to be closed right now. Only pre-case
messages (registration prompts/results, access-denied/pending/stale
notices -- nothing to hang a durable row off yet) are sent directly,
best-effort, mirroring core/self_registration.py's identical precedent.

Customer identity is selected once and locked on the case. Customer-copy
text is built from field allowlists, and outbound PDFs are enqueued by a
stable artifact reference so closed-window retries and duplicate-msgid
replay regenerate identical bytes without repeating business execution.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session as DBSession

from ai.chain import AIProviderChain
from ai.claude_provider import ClaudeProvider
from ai.openai_provider import OpenAIProvider
from clients.kefu_client import KefuAPIError, KefuClient
from core import access_control, kefu_admin_purge, kefu_completion_notice, kefu_registration, kefu_turn_apply, session_manager
from core.kefu_outcomes import (
    ConfirmationAlreadyProcessedOutcome,
    ConfirmationNothingPendingOutcome,
    ConfirmationRecoveringOutcome,
    ServiceListOutcome,
    SessionConflictOutcome,
    UnrecognizedRequestOutcome,
)
from core.kefu_response_renderer import render_kefu_outcome
from core.kefu_contracts import (
    CaseTurnDenied,
    CaseTurnError,
    CaseTurnResult,
    CaseTurnSuccess,
    KefuIdentity,
)
# CaseTurnStale is deliberately never produced by this module (Codex
# round-90 finding 5): it's reserved for a genuine revision-CAS conflict,
# which the current KefuInboundTurn/case_number_hint contract (a bare
# string, no expected-revision field) can't express yet. Unknown/closed/
# unauthorized cases all return CaseTurnDenied instead.
from core.kefu_delivery import (
    Failed,
    QuotaExceeded,
    Retryable,
    Sent,
    TextPayload,
    WindowClosed,
    send_reply,
)

_ai_chain = AIProviderChain(providers=[
    OpenAIProvider(),
    ClaudeProvider(),
])

_OPEN_SESSION_STATUSES = ("active", "pending_confirmation")

# Services with a Kefu-native, single-transaction implementation --
# core.kefu_turn_apply.apply_kefu_turn/confirm_kefu_turn, which never calls
# core.workflow_engine's independently-committing Smart Robot helpers, so a
# duplicate/retried Kefu message can never double-apply a mutation.
# adjust_storage/recount_storage/move_storage were built generically
# alongside the others during the same phase (their PRE_CONFIRM_VALIDATORS
# entry, CONFIRMATION_BUILDERS entry, and HANDLER_REGISTRY dispatch via
# core/uchoice_storage.py's advisory-lock-protected apply_storage_delta
# already existed) but were left off this allowlist as an extra rollout-
# staging precaution, not because their wiring was actually unsafe --
# enabled once that was independently verified end-to-end via Kefu (see
# tests/kefu_integration/test_kefu_storage_correction_services.py).
_KEFU_ENABLED_SERVICES = frozenset({
    "view_storage", "view_storage_history", "view_invoice",
    "view_pending_digest", "explain_service",
    "uchoice_inbound_request", "uchoice_outbound_request", "upsert_address",
    "confirm_inbound_completion", "confirm_outbound_completion",
    "adjust_storage", "recount_storage", "move_storage",
})

# purge_kefu_sessions is DELIBERATELY absent from the set above. It's
# granted via group_service_role purely so explain_service/check_services
# can describe it (see V15's migration comment); its workflow has zero
# steps and nothing ever dispatches through it. If the AI ever
# misclassifies a message as this service, leaving it out of the allowlist
# means _kefu_rollout_denial_reason below denies it before it can reach
# _finish_execution -- which would otherwise silently "complete" with no
# workflow steps to run and no real purge performed, a false-success trap.
# The only real trigger is core/kefu_admin_purge.py's exact-phrase
# pre-AI command.

# check_services must only advertise what a Kefu caller can actually
# invoke. A service can be granted via group_service_role (so it appears
# in context["allowed_services"]) while still being rejected by
# _kefu_rollout_denial_reason below -- adjust_storage/recount_storage/
# move_storage are exactly this today, staged out until they get the same
# Kefu-native confirmation machinery as the enabled set. Listing them
# anyway is misleading: a caller sees the option, tries it, and gets a
# denial that has nothing to do with their role (observed live).
# purge_kefu_sessions is the one deliberate exception in the other
# direction -- excluded from the rollout allowlist above but genuinely
# invokable via its own separate pre-AI command, so it stays visible here.
_KEFU_CHECK_SERVICES_VISIBLE = _KEFU_ENABLED_SERVICES | {"purge_kefu_sessions"}


def _kefu_rollout_denial_reason(context: dict, ai_response, session) -> str | None:
    """Returns a denial reason if this turn would engage a non-allowlisted
    (mutating) service, or None if it's safe to proceed."""
    by_id = {s["service_type_id"]: s["name"] for s in context.get("allowed_services", [])}

    if session is not None and session.service_type_id is not None:
        name = by_id.get(str(session.service_type_id))
        if name is not None and name not in _KEFU_ENABLED_SERVICES:
            return "service_not_enabled_for_kefu"

    if ai_response.service_type_name and ai_response.service_type_name not in _KEFU_ENABLED_SERVICES:
        return "service_not_enabled_for_kefu"

    return None


_CASE_DENIAL_MESSAGES = {
    "case_not_found": "未找到该案件，请核对案件编号。",
    "case_closed": "该案件已结束，无法继续操作。",
    "case_wrong_group": "您无权访问该案件。",
    "case_service_not_granted": "您没有该案件对应服务的权限，请联系管理员。",
    "case_wrong_warehouse": "该案件不属于您负责的仓库。",
}

_EXECUTION_LEASE_MINUTES = 5


class KefuTurnInProgress(RuntimeError):
    """Another attempt currently holds an unexpired claim on this exact msgid."""


def _acquire_execution(db: DBSession, execution_key: str) -> tuple[str, object]:
    """
    Codex round-94 findings 3/4: a REAL guarded claim/lease decision, not
    "return whatever row already exists and let the caller maybe look at
    it." A Postgres advisory lock scoped to execution_key (matching
    core/kefu_sync.py's own claim pattern) is held for the rest of the
    CURRENT transaction, genuinely serializing every concurrent attempt
    for the SAME msgid -- no second executor can proceed past this point
    until the first one's transaction commits or rolls back.

    Returns (action, row):
      "new"         -- fresh (or re-claimed abandoned/failed) claim;
                       caller should run the AI/workflow from scratch.
      "recover"     -- row.status == 'db_committed': a prior attempt's
                       business mutation is durable but its turn never
                       finalized. Caller recovers via row.session_id,
                       never re-runs the AI/workflow.
      "in_progress" -- another attempt holds an unexpired 'claimed' lease.
                       Caller must not proceed.
      "completed"   -- already fully completed. Defensive only: the
                       case_turn-based replay check upstream should
                       already have caught this; reaching here signals a
                       real data inconsistency, not a normal path.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sql_text
    from models.kefu import CaseExecution

    db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": execution_key})

    row = db.query(CaseExecution).filter_by(execution_key=execution_key).first()
    now = datetime.now(timezone.utc)

    if row is None:
        row = CaseExecution(
            execution_key=execution_key,
            status="claimed",
            claimed_by="kefu-case-adapter",
            lease_expires_at=now + timedelta(minutes=_EXECUTION_LEASE_MINUTES),
        )
        db.add(row)
        db.flush()
        return "new", row

    if row.status == "completed":
        return "completed", row

    if row.status == "db_committed":
        return "recover", row

    # status in ("claimed", "failed")
    lease_expired = row.lease_expires_at is None or row.lease_expires_at < now
    if row.status == "claimed" and not lease_expired:
        return "in_progress", row

    # Abandoned claim (expired lease) or a prior failed attempt -- take
    # over. Still under the same advisory lock, so no other concurrent
    # attempt can also be taking over the same row right now.
    row.status = "claimed"
    row.claimed_by = "kefu-case-adapter"
    row.claimed_at = now
    row.lease_expires_at = now + timedelta(minutes=_EXECUTION_LEASE_MINUTES)
    row.db_committed_at = None
    row.completed_at = None
    row.last_error = None
    return "new", row


# Services with no persistent field-collection session of their own --
# they answer and finish within the same turn, so they never compete for
# a staff's "current open case" slot and must never trigger the conflict
# check below, regardless of what else is open.
_READ_ONLY_KEFU_SERVICES = frozenset({
    "view_storage", "view_storage_history", "view_invoice",
    "view_pending_digest", "explain_service",
})


def _detect_session_conflict(context: dict, ai_response, session) -> str | None:
    """
    Stateless, per-turn check: a message just classified as new_request
    for a genuinely different, distinct mutating service than the one the
    staff already has open. Returns the deterministic conflict reply, or
    None if it's safe to proceed normally (no open session, ambiguous/
    continuation content, a read-only service, or the same service the
    open session already has).

    Deliberately holds no state of its own -- nothing is persisted here,
    the old session is never touched, and the triggering message's content
    is discarded either way (the staff resends after resolving). The
    staff's next message, whatever it is, re-enters this exact same check
    from scratch; 取消/继续 are handled entirely by the ALREADY-existing
    cancel/continuation intents, not by anything special this function
    remembers.
    """
    if session is None or session.status not in _OPEN_SESSION_STATUSES:
        return None
    if ai_response.intent != "new_request":
        return None
    new_name = ai_response.service_type_name
    if not new_name or new_name in _READ_ONLY_KEFU_SERVICES:
        return None
    if session.service_type_id is None:
        return None
    allowed = context.get("allowed_services", [])
    if not any(s["name"] == new_name for s in allowed):
        # Unresolvable/hallucinated service name -- not a real second
        # service to conflict against. Let _resolve_turn_service's own
        # unrecognized handling take it from here.
        return None
    by_id = {s["service_type_id"]: s for s in allowed}
    current_service = by_id.get(str(session.service_type_id))
    if current_service is None or current_service["name"] == new_name:
        return None

    from core.kefu_turn_apply import _service_label
    last_question = ""
    for turn in reversed(session.conversation_history or []):
        if turn.get("role") == "assistant":
            last_question = turn.get("content") or ""
            break
    return render_kefu_outcome(SessionConflictOutcome(
        service_label=_service_label(current_service),
        case_number=session.case_number or "",
        last_question=last_question,
    ))


def _resolve_turn_service(context: dict, ai_response, session) -> dict | None:
    """
    Continuing an existing session always uses ITS OWN service (a
    continuation turn's ai_response.service_type_name is meaningless --
    the AI isn't asked to name one). A brand-new turn resolves by the
    AI-named service_type_name against this staff's actual grants.
    """
    if session is not None and session.service_type_id is not None:
        for s in context.get("allowed_services", []):
            if s["service_type_id"] == str(session.service_type_id):
                return s
        return None
    for s in context.get("allowed_services", []):
        if s["name"] == ai_response.service_type_name:
            return s
    return None


def make_case_turn_processor(client: KefuClient, db_factory: Callable[[], DBSession]):
    """Returns a callable matching core.kefu_contracts.CaseTurnProcessor."""

    def process(
        *,
        identity: KefuIdentity,
        message_content: str,
        message_meta: dict,
        case_number_hint: str | None,
    ) -> CaseTurnResult:
        db = db_factory()
        try:
            return _process_turn(db, client, identity, message_content, message_meta, case_number_hint)
        except Exception:
            # Deliberately re-raised, not caught into CaseTurnError:
            # core/kefu_sync.py's run_worker_once already has a mark_failed
            # path keyed on a raised exception from this exact call.
            # Swallowing it here would make the worker call mark_processed
            # instead, mislabeling a real failure as success in
            # kefu_inbound_message's audit trail. CaseTurnError is left
            # defined in the return type for a future caller that wants
            # errors modeled as data (e.g. a synchronous path) but isn't
            # produced by this worker-facing entry point today -- flagged
            # to Codex as an explicit open question, not a settled choice.
            db.rollback()
            raise
        finally:
            db.close()

    return process


def _direct_send(client: KefuClient, identity: KefuIdentity, delivery_key: str, text: str) -> None:
    """
    Best-effort, non-durable send for the two pre-case-existence message
    classes (registration prompts/results, access-denied/pending
    notices) -- there is no kefu_staff row (registration) or no
    case/session (access denied) to hang a durable kefu_outbound_delivery
    row off yet. Mirrors core/self_registration.py's identical precedent
    for Smart Robot: that flow also sends directly, never through
    conversation_session/request_log machinery. Accepts the same
    documented at-least-once risk as every other Kefu send (plan Sec 2.5).
    """
    try:
        state = client.get_service_state(
            open_kfid=identity.open_kfid,
            external_userid=identity.external_userid,
        )
    except KefuAPIError as exc:
        if exc.errcode != 48002:
            raise
        # Some current enterprise-internal Kefu configurations allow
        # sync_msg/send_msg but not the optional conversation-state API.
        # send_msg is authoritative and must not be blocked by this preflight.
        print("[kefu_case_adapter] service-state preflight unavailable (48002); attempting send", flush=True)
    else:
        if state.state not in (0, 1):
            raise RuntimeError(
                f"direct Kefu reply blocked by service_state={state.state}"
                + (f" servicer_userid={state.servicer_userid}" if state.servicer_userid else "")
            )
    result = send_reply(client, recipient=identity, delivery_key=delivery_key, payload=TextPayload(text))
    if isinstance(result, Sent):
        return
    if isinstance(result, WindowClosed):
        error = "window_closed"
    elif isinstance(result, QuotaExceeded):
        error = "quota_exceeded"
    elif isinstance(result, (Retryable, Failed)):
        error = result.error
    else:
        error = type(result).__name__
    raise RuntimeError(f"direct Kefu reply failed: {error}")


def _load_replay_artifacts(session, artifact_keys) -> tuple:
    """Reconstruct stored artifacts without re-running AI or business work."""
    if session is None or not session.request_log_id or not artifact_keys:
        return ()
    from core.kefu_artifact_loader import load_artifact

    artifacts = []
    for key in artifact_keys:
        doc_type = key.rsplit(":", 1)[-1]
        artifacts.append(load_artifact(session.request_log_id, doc_type, key))
    return tuple(artifacts)


def _process_turn(
    db: DBSession,
    client: KefuClient,
    identity: KefuIdentity,
    message_content: str,
    message_meta: dict,
    case_number_hint: str | None,
) -> CaseTurnResult:
    msgid = str(message_meta.get("msgid") or "")

    # Registration is a deterministic pre-access system command -- recognized
    # before check_kefu_access, exactly like core/self_registration.py runs
    # before check_access for Smart Robot. Never touches conversation_session
    # (core/kefu_registration.py's own contract), so nothing to audit-log.
    registration_reply = kefu_registration.try_handle_kefu_registration_command(db, identity, message_content)
    if registration_reply is not None:
        _direct_send(client, identity, f"kefu-registration:{msgid}", registration_reply)
        return CaseTurnSuccess(reply_text=registration_reply, customer_copy_text=None, case_number="", new_revision=0)

    access = access_control.check_kefu_access(db, identity.open_kfid, identity.external_userid)
    if isinstance(access, access_control.AccessDenied):
        if access.notify_user:
            _direct_send(client, identity, f"kefu-denied:{msgid}", access.message)
        return CaseTurnDenied(reason=access.reason)

    pending_reply = kefu_registration.pending_short_circuit_reply(access.role)
    if pending_reply is not None:
        _direct_send(client, identity, f"kefu-pending:{msgid}", pending_reply)
        return CaseTurnDenied(reason="pending_role")

    # Deterministic, pre-AI, admin-only system command -- same shape as
    # registration above: recognized by exact string match only, never
    # touches conversation_session/case machinery, never goes through the
    # AI/execution-ledger pipeline at all. See core/kefu_admin_purge.py.
    purge_reply = kefu_admin_purge.try_handle_purge_command(db, access.role, message_content)
    if purge_reply is not None:
        _direct_send(client, identity, f"kefu-purge:{msgid}", purge_reply)
        return CaseTurnSuccess(reply_text=purge_reply, customer_copy_text=None, case_number="", new_revision=0)

    from models.kefu import CaseTurn, KefuStaff
    staff = db.get(KefuStaff, access.staff_id)

    # Codex round-94: a REAL guarded claim, not "insert-or-return-whatever-
    # exists." Held for the rest of this transaction via an advisory lock
    # -- genuine mutual exclusion per msgid, and the caller now branches on
    # the ledger's own state (not request_log's) to decide recover-vs-new.
    #
    # Codex round-96 finding 1: the replay lookup moved to AFTER acquiring
    # this lock (it used to run before, letting a concurrent duplicate that
    # then blocked on the lock see a stale "nothing here yet" read from
    # before it ever blocked). Now a duplicate that waits genuinely replays
    # the winner's committed result once it wakes up, instead of racing it.
    execution_key = f"kefu:{msgid}" if msgid else None
    execution_row = None
    confirmation_execution_row = None
    service = None
    action = "new"
    if execution_key:
        action, execution_row = _acquire_execution(db, execution_key)

        if msgid:
            existing_turn = db.query(CaseTurn).filter_by(source_message_id=msgid).first()
            if existing_turn is not None:
                from models.session import ConversationSession
                existing_session = db.get(ConversationSession, existing_turn.session_id)
                db.rollback()
                return CaseTurnSuccess(
                    reply_text=existing_turn.reply_text or "",
                    customer_copy_text=existing_turn.customer_copy_text,
                    case_number=existing_session.case_number if existing_session else "",
                    new_revision=existing_turn.case_revision,
                    artifacts=_load_replay_artifacts(existing_session, existing_turn.artifact_keys),
                )

        if action == "in_progress":
            db.rollback()
            raise KefuTurnInProgress(f"execution {execution_key} is already in progress")
        if action == "completed":
            db.rollback()
            raise RuntimeError(
                f"case_execution {execution_key} is marked completed but no case_turn was found "
                "for this msgid -- data inconsistency, not a normal path"
            )

    session = _resolve_kefu_session(db, access, case_number_hint)
    if isinstance(session, CaseTurnDenied):
        db.rollback()
        _direct_send(client, identity, f"kefu-case-denied:{msgid}", _CASE_DENIAL_MESSAGES.get(
            session.reason, "无法处理该案件，请联系管理员。"
        ))
        return session

    message = {"content": message_content, "msg_id": msgid, "response_url": ""}

    if action == "recover":
        # Codex round-94 finding 4: recovery is now inferred from the
        # LEDGER's own db_committed state, not by re-deriving it from
        # request_log. A prior attempt's business mutation is durable
        # (core/kefu_turn_apply.py flips this row to db_committed in the
        # SAME transaction/commit as the request_log/session it certifies
        # -- see that module for the actual atomicity boundary) but the
        # turn never finalized. The original AI reply is genuinely
        # unrecoverable (only ever held in memory); recovery answers with
        # a safe, generic status message and skips the AI/workflow
        # entirely -- re-running them would attempt a second request_log
        # insert and crash again on its UNIQUE constraint.
        from models.session import ConversationSession
        recovered_session = db.get(ConversationSession, execution_row.session_id) if execution_row.session_id else None
        if recovered_session is None:
            db.rollback()
            raise RuntimeError(f"execution {execution_key} is db_committed but its session is missing")
        context = session_manager.build_context(db, access, recovered_session, message)
        reply_text = "您的请求已收到并正在处理，如需查询进度请稍后重试或联系管理员。"
    else:
        context = session_manager.build_context(db, access, session, message)
        ai_response = _ai_chain.process(context)

        denial_reason = _kefu_rollout_denial_reason(context, ai_response, session)
        if denial_reason is not None:
            if execution_row is not None:
                execution_row.status = "failed"
                execution_row.last_error = "rollout_denied"
            db.commit()
            _direct_send(client, identity, f"kefu-rollout-denied:{msgid}", (
                "该服务暂未在企业微信客服开放，请通过其他渠道办理，或联系管理员。"
            ))
            return CaseTurnDenied(reason=denial_reason)

        if ai_response.intent == "cancel":
            service = _resolve_turn_service(context, ai_response, session) if session is not None else None
            reply_text = kefu_turn_apply.cancel_kefu_turn(db, context, service, session)
        elif ai_response.intent == "confirm":
            service = _resolve_turn_service(context, ai_response, session)
            if session is None or service is None:
                reply_text = render_kefu_outcome(ConfirmationNothingPendingOutcome())
            else:
                logical_key = f"kefu-confirm:{session.session_id}:{session.case_revision or 0}"
                confirm_action, confirmation_execution_row = _acquire_execution(db, logical_key)
                # The advisory-lock wait may have outlived the ORM snapshot.
                # No local changes have been made to the session yet, so this
                # refresh is safe and makes a concurrent winner visible.
                db.refresh(session)
                if confirm_action == "completed" or session.status != "pending_confirmation":
                    reply_text = render_kefu_outcome(ConfirmationAlreadyProcessedOutcome())
                    confirmation_execution_row = None
                elif confirm_action == "in_progress":
                    raise KefuTurnInProgress(f"execution {logical_key} is already in progress")
                elif confirm_action == "recover":
                    # The Kefu-native path stores the business mutation and
                    # durable delivery enqueue in one transaction, so it
                    # cannot normally leave this intermediate state. Treat it
                    # as a recovery-only completion without re-running DB work.
                    reply_text = render_kefu_outcome(ConfirmationRecoveringOutcome())
                else:
                    confirmation_execution_row.session_id = session.session_id
                    reply_text = kefu_turn_apply.confirm_kefu_turn(db, context, service, session)
        elif ai_response.intent in ("new_request", "continuation"):
            conflict_reply = _detect_session_conflict(context, ai_response, session)
            if conflict_reply is not None:
                # Deliberately bypasses _finalize_turn -- that path performs
                # a real CAS bump of the OLD session's case_revision, which
                # would touch state this outcome must leave completely
                # alone. Sent best-effort, same as unrecognized/
                # check_services replies below, which have the same
                # "nothing to attach this to" shape.
                if execution_row is not None:
                    execution_row.status = "completed"
                    from datetime import datetime, timezone
                    execution_row.completed_at = datetime.now(timezone.utc)
                    if execution_row.db_committed_at is None:
                        execution_row.db_committed_at = execution_row.completed_at
                db.commit()
                _direct_send(client, identity, f"kefu-reply:{msgid}", conflict_reply)
                return CaseTurnSuccess(reply_text=conflict_reply, customer_copy_text=None, case_number="", new_revision=0)
            else:
                service = _resolve_turn_service(context, ai_response, session)
                if service is None:
                    if execution_row is not None:
                        execution_row.status = "failed"
                        execution_row.last_error = "no_service_resolved"
                    db.commit()
                    reply_text = render_kefu_outcome(UnrecognizedRequestOutcome())
                    _direct_send(client, identity, f"kefu-reply:{msgid}", reply_text)
                    return CaseTurnSuccess(reply_text=reply_text, customer_copy_text=None, case_number="", new_revision=0)
                if execution_key:
                    context["_kefu_execution_key"] = execution_key
                reply_text = kefu_turn_apply.apply_kefu_turn(db, context, ai_response, service, session)
        else:
            # check_services / unrecognized (confirm is handled in its own
            # elif above): no service/session mutation, just a deterministic
            # routing reply.
            if ai_response.intent == "check_services":
                from core.confirmation import build_display_name
                from core.kefu_outcomes import ServiceListEntry
                entries = tuple(
                    ServiceListEntry(
                        label=build_display_name(s["name"], {}),
                        keywords=tuple(s.get("keywords") or ()),
                    )
                    for s in context.get("allowed_services", [])
                    if s["name"] in _KEFU_CHECK_SERVICES_VISIBLE
                )
                reply_text = render_kefu_outcome(ServiceListOutcome(entries=entries)) if entries else render_kefu_outcome(UnrecognizedRequestOutcome())
            else:
                reply_text = render_kefu_outcome(UnrecognizedRequestOutcome())

    new_session_id = context.get("session_id")
    if new_session_id and staff is not None:
        # Codex round-90 findings 1/2/6: notice-claim, case_turn audit
        # (reply stored on the msgid-bearing row), durable delivery
        # enqueue, and the notice's shown-at mark all land in the SAME
        # final commit as this turn's business state and the execution
        # ledger's completion below -- a failure partway through rolls
        # back everything in this unit, nothing partial survives.
        reply_text, case_number, case_revision = _finalize_turn(
            db, client, staff, new_session_id, message_content, msgid, reply_text,
            service_name=context.get("_effective_service_name") or (service["name"] if service else None),
            context=context,
        )
        customer_copy_text = context.get("_customer_copy_text")
        artifacts = context.get("_final_artifacts", ())
    else:
        _direct_send(client, identity, f"kefu-reply:{msgid}", reply_text)
        customer_copy_text, artifacts, case_number, case_revision = None, (), "", 0

    if execution_row is not None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        execution_row.status = "completed"
        execution_row.completed_at = now
        if execution_row.db_committed_at is None:
            execution_row.db_committed_at = now

    if confirmation_execution_row is not None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        confirmation_execution_row.status = "completed"
        confirmation_execution_row.db_committed_at = now
        confirmation_execution_row.completed_at = now

    db.commit()

    return CaseTurnSuccess(
        reply_text=reply_text,
        customer_copy_text=customer_copy_text,
        case_number=case_number,
        new_revision=case_revision,
        artifacts=artifacts,
    )


def _authorize_case(access, session) -> str | None:
    """
    Codex round-90 finding 5: a resolved case must be reauthorized against
    the CURRENT staff's actual grants on every turn -- their role/warehouse
    could have changed since the case was opened or last touched. Returns
    a denial reason, or None if authorized.
    """
    if str(session.group_id) != str(access.group_id):
        return "case_wrong_group"
    if session.service_type_id is not None:
        allowed_ids = {s["service_type_id"] for s in access.allowed_services}
        if str(session.service_type_id) not in allowed_ids:
            return "case_service_not_granted"
    if access.warehouse_code is not None:
        session_warehouse = (session.collected_fields or {}).get("warehouse_code")
        if session_warehouse is not None and session_warehouse != access.warehouse_code:
            return "case_wrong_warehouse"
    return None


def _resolve_kefu_session(db: DBSession, access, case_number_hint: str | None):
    """
    Returns the session to continue, None for a brand-new case, or a
    CaseTurnDenied if an explicitly-referenced case_number_hint doesn't
    resolve to a live, authorized case for this staff member.

    Codex round-90 finding 5's correction: unknown, terminal (closed), and
    unauthorized (wrong group/service-grant/warehouse) cases are all
    Denied, not Stale -- Stale is reserved for a genuine revision-CAS
    conflict, which the current KefuInboundTurn/case_number_hint contract
    (a bare string, no expected-revision field) can't even express yet.

    Priority: an explicit case_number_hint (staff pasted "CASE-...") always
    wins over the staff's own current-case binding -- deliberate, matches
    plan Sec 2.5: an explicit reference is a stronger signal than whatever
    the staff happened to be working on last. No hint falls back to
    kefu_staff_case_context.active_session_id (Sec 2.5's unqualified-
    follow-up mechanism), reauthorized identically on every turn.
    """
    from models.session import ConversationSession

    if case_number_hint:
        session = db.query(ConversationSession).filter_by(case_number=case_number_hint).first()
        if session is None:
            return CaseTurnDenied(reason="case_not_found")
        if session.status not in _OPEN_SESSION_STATUSES:
            return CaseTurnDenied(reason="case_closed")
        denial = _authorize_case(access, session)
        if denial is not None:
            return CaseTurnDenied(reason=denial)
        return session

    from models.kefu import KefuStaffCaseContext
    binding = db.get(KefuStaffCaseContext, access.staff_id)
    if binding is None or binding.active_session_id is None:
        return None
    session = db.get(ConversationSession, binding.active_session_id)
    if session is None or session.status not in _OPEN_SESSION_STATUSES:
        return None
    if _authorize_case(access, session) is not None:
        # An implicit binding gone stale (role/warehouse changed) silently
        # falls back to a brand-new case rather than blocking the turn --
        # unlike an explicit hint, the staff never asked for this specific
        # case by name, so there's nothing to deny; starting fresh is the
        # correct, silent recovery.
        return None
    return session


def _finalize_turn(
    db: DBSession,
    client: KefuClient,
    staff,
    session_id,
    message_content: str,
    msgid: str,
    reply_text: str,
    *,
    service_name: str | None = None,
    context: dict | None = None,
) -> tuple[str, str, int]:
    """
    Builds everything a real case turn needs -- case_number/case_revision
    (genuine CAS), a claimed completion notice, the msgid-bearing case_turn
    audit row, the staff's case-context binding, and the durable delivery
    enqueue -- via db.add()/direct mutation only. Does NOT commit: the
    caller (_process_turn) commits exactly once, bundling this together
    with the execution ledger's completion, per Codex round-94's "explicit
    atomic boundaries" requirement. Any exception here propagates all the
    way up uncaught -- make_case_turn_processor's outer except rolls back
    the whole unit (nothing partial survives, including the notice claim)
    and re-raises, which is what surfaces it to core/kefu_sync.py's
    run_worker_once as a failed message.

    Returns the historical ``(staff_reply, case_number, revision)`` tuple.
    New structured outputs are placed on ``context`` so existing callers of
    this internal helper remain compatible.
    """
    from sqlalchemy import text as sql_text
    from models.kefu import CaseTurn, KefuStaffCaseContext
    from models.session import ConversationSession
    from core.kefu_delivery import enqueue_file, enqueue_text
    from core.kefu_customer_copy import compose_staff_reply, render_customer_copy

    session = db.get(ConversationSession, session_id)
    if session is None:
        _direct_send(client, KefuIdentity(staff.open_kfid, staff.external_userid), f"kefu-reply:{msgid}", reply_text)
        return reply_text, "", 0

    # source_channel/opened_by_staff_id are set at session-creation time
    # (session_manager.create_session, via context["source_channel"]/
    # ["submitted_by_staff_id"] -- Codex round-90 finding 4), not patched
    # in here after the fact.
    #
    # Codex round-92: genuine revision-CAS, not a plain ORM increment --
    # two staff members can both reference the same case_number (an
    # explicit hint is not staff-exclusive) and both reach this point
    # concurrently. The guarded UPDATE...WHERE case_revision=:expected
    # only succeeds for whichever transaction commits first; the loser's
    # rowcount is 0, which is treated as a genuine conflict and raised
    # (rolling back everything in this unit, including its notice claim)
    # rather than silently overwriting the other transaction's work.
    expected_revision = session.case_revision or 0
    new_case_number = session.case_number or db.execute(sql_text("SELECT generate_case_number()")).scalar()
    cas_result = db.execute(
        sql_text(
            "UPDATE conversation_session "
            "SET case_revision = :new_rev, case_number = :case_number "
            "WHERE session_id = :sid AND case_revision = :expected_rev"
        ),
        {
            "new_rev": expected_revision + 1,
            "case_number": new_case_number,
            "sid": session.session_id,
            "expected_rev": expected_revision,
        },
    )
    if cas_result.rowcount == 0:
        # Not rolled back here -- the caller (_process_turn) owns the
        # single commit/rollback boundary for the whole turn now; raising
        # propagates to make_case_turn_processor's outer except, which
        # rolls back everything in this unit, including the notice claim.
        raise RuntimeError(f"case_revision_conflict: session {session.session_id} was concurrently modified")
    # Deliberately NOT db.refresh(session): SessionLocal is autoflush=False
    # (database.py), so a blind refresh() reads the row as it currently
    # stands in the DB and overwrites every OTHER pending, not-yet-flushed
    # attribute on this same object -- silently discarding
    # core/kefu_turn_apply.py's still-unflushed session.status/
    # collected_fields/conversation_history changes from earlier in this
    # same turn. We already know exactly what the CAS UPDATE just wrote,
    # so set those two columns directly instead of re-reading the row.
    session.case_revision = expected_revision + 1
    session.case_number = new_case_number

    claimed_notice_log = kefu_completion_notice.lock_pending_completion_notice(db, staff)
    if claimed_notice_log is not None:
        notice = kefu_completion_notice.notice_text(db, claimed_notice_log)
        reply_text = f"{notice}\n\n{reply_text}" if reply_text else notice

    from models.request_log import RequestLog
    log = db.get(RequestLog, session.request_log_id) if session.request_log_id else None
    context = context or {}
    customer_copy_text = render_customer_copy(
        db,
        service_name=service_name,
        session=session,
        request_log=log,
        context=context,
    )
    reply_text = compose_staff_reply(reply_text, customer_copy_text)
    artifact_entries = context.get("_kefu_artifacts") or []
    artifacts = tuple(entry["artifact"] for entry in artifact_entries)
    artifact_keys = [artifact["artifact_key"] for artifact in artifacts] or None
    context["_customer_copy_text"] = customer_copy_text
    context["_final_artifacts"] = artifacts

    if msgid:
        db.add(CaseTurn(
            session_id=session.session_id,
            case_revision=session.case_revision,
            acting_staff_id=staff.staff_id,
            role="user",
            source_message_id=msgid,
            content=message_content,
            reply_text=reply_text,
            customer_copy_text=customer_copy_text,
            artifact_keys=artifact_keys,
        ))
        db.add(CaseTurn(
            session_id=session.session_id,
            case_revision=session.case_revision,
            role="assistant",
            reply_text=reply_text,
            content=reply_text,
        ))

    still_open = session.status in _OPEN_SESSION_STATUSES
    binding = db.get(KefuStaffCaseContext, staff.staff_id)
    if still_open:
        if binding is None:
            db.add(KefuStaffCaseContext(staff_id=staff.staff_id, active_session_id=session.session_id))
        else:
            binding.active_session_id = session.session_id
    else:
        # A case can be handled by more than one authorized staff member.
        # Closing it must clear every stale binding, not only today's actor.
        db.query(KefuStaffCaseContext).filter_by(active_session_id=session.session_id).update(
            {"active_session_id": None}, synchronize_session="fetch"
        )

    delivery_key = f"kefu-reply:{msgid}" if msgid else f"kefu-reply:{session.session_id}:{reply_text[:32]}"
    enqueue_text(
        db,
        recipient_staff_id=staff.staff_id,
        idempotency_key=delivery_key,
        text_content=reply_text,
        session_id=session.session_id,
    )

    if log is not None:
        for entry in artifact_entries:
            artifact = entry["artifact"]
            doc_type = entry["doc_type"]
            enqueue_file(
                db,
                recipient_staff_id=staff.staff_id,
                idempotency_key=f"kefu-file:{artifact['artifact_key']}:{staff.staff_id}",
                request_log_id=log.log_id,
                doc_type=doc_type,
                artifact=artifact,
            )

    if claimed_notice_log is not None:
        from datetime import datetime, timezone
        claimed_notice_log.completion_notice_shown_at = datetime.now(timezone.utc)

    # No commit here -- _process_turn commits once, after this returns,
    # bundling this unit together with the execution ledger's completion.
    return reply_text, (session.case_number or ""), session.case_revision
