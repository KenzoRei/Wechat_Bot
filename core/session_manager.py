import re
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session as DBSession
from models.session import ConversationSession
from models.request_log import RequestLog
from core.access_control import AccessResult
from core import uchoice_context
import config

SERIAL_PATTERN = re.compile(r'REQ-\d{8}-\d{6}')


def extract_serial_from_message(content: str) -> str | None:
    match = SERIAL_PATTERN.search(content)
    return match.group(0) if match else None


def find_session_by_serial(db: DBSession, serial_number: str) -> ConversationSession | None:
    log = db.query(RequestLog).filter_by(serial_number=serial_number).first()
    if log is None or log.session_id is None:
        return None
    return db.query(ConversationSession).filter(
        ConversationSession.session_id == log.session_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def find_current_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID
) -> ConversationSession | None:
    """Returns the one in-progress session for this user in this group, or None."""
    return db.query(ConversationSession).filter(
        ConversationSession.wechat_openid == wechat_openid,
        ConversationSession.group_id == group_id,
        ConversationSession.status.in_(['active', 'pending_confirmation'])
    ).first()


def resolve_session(
    db: DBSession,
    access: AccessResult,
    content: str
) -> ConversationSession | None:
    """
    Returns the in-progress session if one exists, else None.
    Serial number fast path first; falls back to user+group lookup.
    AI always decides the final intent — this only loads context.
    """
    serial = extract_serial_from_message(content)
    if serial:
        session = find_session_by_serial(db, serial)
        if session:
            return session

    return find_current_session(db, access.wechat_openid, access.group_id)


def create_session(
    db: DBSession,
    wechat_openid: str,
    group_id: UUID,
    initial_message: str,
    service_type_id: UUID | None = None,
    source_channel: str = "smart_robot",
    opened_by_staff_id: UUID | None = None,
) -> ConversationSession:
    # kefu-migration-plan.md Sec 2.4 / Codex round-90 finding 4: set at
    # creation, not patched in by a later, separate transaction -- see
    # request_logger.create_log()'s identical note.
    session = ConversationSession(
        wechat_openid=wechat_openid,
        group_id=group_id,
        service_type_id=service_type_id,
        status="active",
        conversation_history=[{"role": "user", "content": initial_message}],
        collected_fields={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES),
        source_channel=source_channel,
        opened_by_staff_id=opened_by_staff_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_message(
    db: DBSession,
    session: ConversationSession,
    role: str,
    content: str
) -> None:
    """Appends a message to history and resets the expiry timer."""
    session.conversation_history = session.conversation_history + [
        {"role": role, "content": content}
    ]
    session.updated_at = datetime.now(timezone.utc)
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_EXPIRY_MINUTES)
    db.commit()


def update_collected_fields(
    db: DBSession,
    session: ConversationSession,
    fields: dict
) -> None:
    session.collected_fields = {**session.collected_fields, **fields}
    db.commit()


def close_session(
    db: DBSession,
    session: ConversationSession,
    status: str,  # "completed" | "cancelled" | "rejected" | "failed" | "timed_out"
    commit: bool = True
) -> None:
    """
    commit=False lets a caller (core.workflow_engine's atomic DB phase) fold
    this status change into the same transaction as the storage deltas and
    mark_success/mark_failed call that surround it, so the whole unit commits
    or rolls back together (systemic-validation-addendum.md Sec 3b/Codex
    round-28 finding 1).
    """
    session.status = status
    session.updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()


def build_context(
    db: DBSession,
    access: AccessResult,
    session: ConversationSession | None,
    message: dict
) -> dict:
    """Assembles the full context dict passed through the entire pipeline."""
    return {
        # from access_control
        "wechat_openid":     access.wechat_openid,
        "group_id":          str(access.group_id),
        "role":              access.role,
        "display_name":      access.display_name,
        "warehouse_code":    access.warehouse_code,
        "allowed_services":  access.allowed_services,
        "group_context":     access.group_context,
        "group_description": access.group_description,
        # kefu-migration-plan.md Sec 2.4 / Codex round-90 finding 4 --
        # carried through so create_session/create_log can set the right
        # provenance at creation time instead of defaulting to
        # 'smart_robot' and needing a later patch-up.
        "source_channel":        access.source_channel,
        "submitted_by_staff_id": str(access.staff_id) if access.staff_id else None,

        # from session (None if not yet created)
        "session_id":           str(session.session_id) if session else None,
        "session_status":       session.status if session else None,
        "serial_number":        None,
        "service_type_id":      str(session.service_type_id) if session and session.service_type_id else None,
        "conversation_history": session.conversation_history if session else [],
        "collected_fields":     session.collected_fields if session else {},
        # kefu-migration-plan.md Sec 6.2: the case's own locked customer
        # (None until resolved -- see core/uchoice_customer.py). Read by
        # handlers/uchoice/address.py; unaffected for Smart Robot, which has
        # no customer_id concept and never sets session.customer_id at all.
        "customer_id": str(session.customer_id) if session and session.customer_id else None,

        # candidate-list context injection (addresses, pending requests,
        # storage buckets, member list) — scoped to whichever services this
        # caller's role can actually trigger
        "uchoice_candidates": _build_uchoice_candidates(db, access, session),

        # from webhook_receiver
        "content":      message["content"],
        "msg_id":       message["msg_id"],
        "response_url": message.get("response_url", ""),

        # filled downstream
        "parsed_input":   None,
        "request_log_id": None,
        "result":         None,
        "error_detail":   None,
    }


def _build_uchoice_candidates(
    db: DBSession,
    access: AccessResult,
    session: ConversationSession | None
) -> dict:
    """
    Conditionally fetches candidate lists based on which service names this
    caller's role can trigger — no point injecting the member list for a
    customer who could never call role_change.

    Once a session already has a locked-in service (mid-collection or
    pending_confirmation), candidates are scoped to just that one service
    instead of the caller's full role-wide set. Injecting everything the
    role can ever reach — SKUs, pending inbound/outbound requests,
    addresses, members — into every turn of an already-committed session
    has been observed live to distract the AI into referencing an unrelated
    candidate (e.g. surfacing a stray pending outbound request mid-way
    through an unrelated view_invoice session) instead of correctly handling
    the actual turn, including cancel. The full role-wide set is still used
    for a brand-new session, where the service itself hasn't been chosen yet
    and the AI legitimately needs the full picture to route the request.
    """
    by_name = {s["name"]: s["service_type_id"] for s in access.allowed_services}
    by_id = {s["service_type_id"]: s["name"] for s in access.allowed_services}

    if session is not None and session.service_type_id:
        active_name = by_id.get(str(session.service_type_id))
        names = {active_name} if active_name else set()
    else:
        names = {s["name"] for s in access.allowed_services}
    collected = session.collected_fields if session else {}
    scope_warehouse = collected.get("warehouse_code") or access.warehouse_code

    candidates: dict = {}

    SKU_DEPENDENT_SERVICES = {
        "uchoice_inbound_request", "uchoice_outbound_request",
        "adjust_storage", "recount_storage", "move_storage",
        "view_storage", "view_storage_history",
    }
    if names & SKU_DEPENDENT_SERVICES:
        # Defaults to JFK when the warehouse isn't known yet — same default
        # used for uchoice_outbound_request's warehouse_code itself (real
        # customers essentially never state it), so the stock signal used
        # for SKU disambiguation matches whichever warehouse the request
        # will actually end up defaulting to.
        candidates["skus"] = uchoice_context.sku_catalog(db, scope_warehouse or "JFK")

    # kefu-migration-plan.md Sec 6.2 (superseded): customer selection used to
    # be required for Kefu inbound/outbound requests. The user corrected this
    # directly: every current U-Choice service is performed on behalf of
    # U-Choice itself (the sole platform tenant today) -- `customer_id`
    # identifies a future second TENANT (the role group_id plays for Smart
    # Robot), not "which address-book company is this for". No current
    # service injects a customer candidate list. core/uchoice_context
    # .customer_candidates() and core/uchoice_customer.resolve_and_lock_
    # customer remain as dormant, reusable infrastructure for whenever a
    # real second tenant exists (see docs/ai-collaboration/decisions.md's
    # "Superseded or challenged assumptions").

    # kefu-deterministic-response-plan.md Sec 5: the shared U-Choice address
    # book is injected immediately for every authorized service that can
    # match against it, on BOTH channels, never filtered/withheld by
    # customer_id. Round 99 built the opposite rule (withhold until a
    # customer is locked, then filter to it) on the mistaken belief that
    # addresses were customer-private; the user corrected this after the
    # live incident that motivated this phase (see decisions.md's
    # "Superseded or challenged assumptions") -- customer_id is provenance/
    # reporting metadata only, never an address ACL. upsert_address needs
    # the same candidate list to resolve create-vs-update via
    # matched_address_id, so it's included here too (previously a gap: it
    # received no address candidates at all).
    if names & {"uchoice_outbound_request", "upsert_address"}:
        # Scoped to the request's own source warehouse ONLY when
        # uchoice_outbound_request is the sole possible service this turn --
        # upsert_address needs the full, unfiltered book to resolve
        # create-vs-update, so any turn where it's still a live possibility
        # (a brand-new, not-yet-service-locked session) must not filter.
        # scope_warehouse mirrors the SKU catalog scoping above/default-JFK
        # reasoning: real customers essentially never state warehouse_code,
        # so candidates should reflect whichever warehouse the request will
        # actually end up defaulting to.
        if names == {"uchoice_outbound_request"}:
            candidates["addresses"] = uchoice_context.address_candidates(db, source_warehouse_code=scope_warehouse or "JFK")
        else:
            candidates["addresses"] = uchoice_context.address_candidates(db)
        # boxes_per_pallet resolution (default-fill, ambiguity-clarification,
        # stock-sufficiency check) is entirely code-level now — see
        # workflow_engine._resolve_outbound_pallet_defaults — so the AI no
        # longer needs real bucket numbers in context at all. Not injecting
        # them removes the exact material that kept tempting it to self-fill
        # a plausible-looking value despite being told not to.

    if "confirm_inbound_completion" in names and "uchoice_inbound_request" in by_name:
        candidates["pending_inbound_requests"] = uchoice_context.pending_request_candidates(
            db, scope_warehouse, [by_name["uchoice_inbound_request"]]
        )

    if "confirm_outbound_completion" in names and "uchoice_outbound_request" in by_name:
        candidates["pending_outbound_requests"] = uchoice_context.pending_request_candidates(
            db, scope_warehouse, [by_name["uchoice_outbound_request"]]
        )

    if "role_change" in names:
        candidates["members"] = uchoice_context.member_candidates(db, access.group_id)

    if "explain_service" in names:
        # Scoped to this group's own granted services — NOT system-wide.
        # This platform is multi-tenant (each WeCom group belongs to a
        # different client); explain_service was originally built with an
        # unrestricted system-wide catalog on the reasoning that read-only
        # info about a service isn't sensitive, but that reasoning only
        # holds for role-scoping within one client. Across clients it leaks
        # which other clients/services exist on the platform at all — found
        # live when a U-Choice group's admin could see yestech's fedex_label/
        # ups_label. group_service already tracks exactly what's granted to
        # this group, so reuse it instead of a separate unrestricted query.
        candidates["service_catalog"] = [
            {"name": s["name"], "description": s.get("description") or "", "keywords": s.get("keywords") or []}
            for s in access.allowed_services
        ]

    return candidates
