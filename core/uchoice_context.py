"""
Candidate-list context injection for U-Choice — fetches small, scoped
candidate lists fresh on every incoming message, for the AI to fuzzy-match
against in its single-shot call. Not tool-calling: this is pre-fetched
context, the same mechanism as the existing group_context/location_presets
block in ai/prompt_builder.py, just generalized to four more lists.
"""
from sqlalchemy.orm import Session as DBSession
from models.uchoice import UchoiceAddress, UchoiceStorage, UchoiceSku
from models.request_log import RequestLog
from models.group import GroupMember
from models.role import Role


def format_address_label(addr) -> str:
    """
    company_name is optional (V12) — several real addresses are only ever
    known by a bare address or a location nickname, no formal company name.
    Shared by every place that renders an address for display, so a missing
    company_name degrades to just the address instead of literally printing
    "None（...）".
    """
    if addr is None:
        return "未知地址"
    if addr.company_name:
        return f"{addr.company_name}（{addr.addr}）"
    return addr.addr


def sku_catalog(db: DBSession, warehouse_code: str | None = None) -> list[dict]:
    """
    All 8 U-Choice SKUs — cheap, full table every time (no scoping needed).
    Lets the AI resolve a free-text product description (e.g. "2寸透明胶带")
    to the real sku_code (e.g. "t4") instead of inventing one from the
    customer's own words.

    When warehouse_code is given, each entry also carries in_stock — real
    customer wording is sometimes ambiguous between two catalog entries with
    overlapping descriptions (e.g. "棕色胶带" could mean either t2 "Dark
    Brown" or t3 "Light Brown" Packing Tape), and current stock is a strong,
    real disambiguating signal: if only one of the plausible matches is
    actually stocked, that's almost certainly the one meant.
    """
    rows = db.query(UchoiceSku).all()
    if warehouse_code is None:
        return [{"sku_code": s.sku_code, "description": s.description} for s in rows]

    buckets = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
    stocked_skus = {b.sku_code for b in buckets if b.pallet_count > 0}
    return [
        {"sku_code": s.sku_code, "description": s.description, "in_stock": s.sku_code in stocked_skus}
        for s in rows
    ]


def sku_label_map(db: DBSession) -> dict[str, str]:
    """
    sku_code -> human-readable description, e.g. 't4' -> 'T4 2-inch Clear
    Packing Tape'. Shared by core/confirmation.py and core/result_message.py
    so both resolve product names identically rather than duplicating the
    lookup query.
    """
    return {s.sku_code: s.description for s in db.query(UchoiceSku).all()}


def address_candidates(db: DBSession, customer_id=None) -> list[dict]:
    """
    kefu-deterministic-response-plan.md Sec 5: the U-Choice address book is
    shared across every authorized U-Choice service -- `customer_id` is
    provenance/reporting metadata only, never an address visibility ACL.
    Neither current caller (Smart Robot's own uchoice_outbound_request path,
    or Kefu's session_manager._build_uchoice_candidates) passes a
    customer_id filter.

    The optional customer_id filter itself is kept, not removed, for any
    future legacy/reporting caller that genuinely wants a customer-scoped
    subset -- it is no longer how visibility/matching is gated for any
    request-submission path (round-99's opposite design -- withhold until a
    customer is locked, then filter to it -- was reverted after a live
    incident showed it contradicted the actual business rule; see
    docs/ai-collaboration/decisions.md's "Superseded or challenged
    assumptions").
    """
    query = db.query(UchoiceAddress)
    if customer_id is not None:
        query = query.filter_by(customer_id=customer_id)
    rows = query.all()
    return [
        {
            "address_id":     str(a.address_id),
            "company_name":   a.company_name,
            "charge_type":    a.charge_type,
            "addr":           a.addr,
            "warehouse_code": a.warehouse_code,
            "note":           a.note,
        }
        for a in rows
    ]


def customer_candidates(db: DBSession) -> list[dict]:
    """
    kefu-migration-plan.md Sec 6.2: the full active customer directory, for
    the AI to fuzzy-match a Kefu staff member's free-text customer
    description against -- same pattern as sku_catalog()/address_candidates()
    above. Cheap, full-table query (the directory is small); no scoping
    needed since this IS the thing being scoped by (nothing to filter it by
    yet -- that's exactly what resolving this candidate produces).
    """
    from models.kefu import UchoiceCustomer
    rows = db.query(UchoiceCustomer).filter_by(is_active=True).all()
    return [
        {
            "customer_id":    str(c.customer_id),
            "customer_code":  c.customer_code,
            "canonical_name": c.canonical_name,
            "aliases":        c.aliases,
        }
        for c in rows
    ]


def _summarize_sku_lines(lines: list[dict], sku_labels: dict[str, str]) -> str:
    """Compact human-readable summary for candidate-list display, e.g. 'S2 x11托, T2 x4托'."""
    from collections import defaultdict

    palletized_totals: dict[str, int] = defaultdict(int)
    loose_totals: dict[str, int] = defaultdict(int)
    for line in lines or []:
        sku = line.get("sku_code", "?")
        if "box_count" in line:
            loose_totals[sku] += line["box_count"]
        elif "pallet_count" in line:
            palletized_totals[sku] += line["pallet_count"]

    parts = [f"{sku_labels.get(sku, sku)} x{qty}托" for sku, qty in sorted(palletized_totals.items())]
    parts += [f"{sku_labels.get(sku, sku)} 散箱x{qty}" for sku, qty in sorted(loose_totals.items())]
    return "，".join(parts) if parts else "（无商品明细）"


def pending_request_candidates(db: DBSession, warehouse_code: str | None, service_type_ids: list[str]) -> list[dict]:
    """
    Requests still awaiting warehouse completion (status='processing') for the
    given service types (inbound or outbound request types), scoped to one
    warehouse (the confirming warehouseman's own) when provided. Includes
    enough of the original submission (warehouse, SKU summary, and for
    outbound the destination) for the AI to actually describe each candidate
    to the user — a bare serial_number gives them nothing to recognize which
    request is which.
    """
    if not service_type_ids:
        return []
    rows = (
        db.query(RequestLog)
        .filter(
            RequestLog.status == "processing",
            RequestLog.service_type_id.in_(service_type_ids),
        )
        .order_by(RequestLog.created_at.asc())
        .all()
    )

    sku_labels = sku_label_map(db)
    candidates = []
    for r in rows:
        original_fields = get_original_fields(db, r)
        req_warehouse_code = original_fields.get("warehouse_code")
        if warehouse_code and req_warehouse_code and req_warehouse_code != warehouse_code:
            continue

        candidate = {
            "serial_number":  r.serial_number,
            "wechat_openid":  r.wechat_openid,
            "created_at":     r.created_at.isoformat() if r.created_at else None,
            "warehouse_code": req_warehouse_code,
            "sku_summary":    _summarize_sku_lines(original_fields.get("sku_lines", []), sku_labels),
        }

        destination_address_id = original_fields.get("destination_address_id")
        if destination_address_id:
            addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
            if addr:
                candidate["destination"] = format_address_label(addr)

        candidates.append(candidate)

    return candidates


def resolve_loose_pick_defaults(
    db: DBSession, warehouse_code: str | None, sku_code: str, box_count_needed: int
) -> list[dict] | None:
    """
    Greedily fills a loose outbound pick from the smallest-boxes_per_pallet
    bucket first (use up small/odd pallets before opening a bigger one),
    spanning multiple buckets if one alone doesn't cover the requested
    amount. Returns None if total available stock across all buckets for
    this sku+warehouse can't cover box_count_needed at all — caller should
    treat that as a blocking condition, not silently under-fulfill.
    """
    buckets = (
        db.query(UchoiceStorage)
        .filter_by(warehouse_code=warehouse_code, sku_code=sku_code)
        .filter(UchoiceStorage.pallet_count > 0)
        .order_by(UchoiceStorage.boxes_per_pallet.asc())
        .all()
    )
    picks = []
    remaining = box_count_needed
    for b in buckets:
        if remaining <= 0:
            break
        available = b.boxes_per_pallet * b.pallet_count
        take = min(available, remaining)
        picks.append({"source_boxes_per_pallet": b.boxes_per_pallet, "box_count": take})
        remaining -= take
    if remaining > 0:
        return None
    return picks


def get_original_fields(db: DBSession, target) -> dict:
    """
    Given an already-fetched target RequestLog, resolves its original
    submitter's collected_fields (warehouse_code, sku_lines, etc.) via their
    ConversationSession. Shared by LookupAndValidateCompletionHandler
    (execution-time) and core/confirmation.py's completion builders
    (confirm-time display) so both resolve the original request identically.

    kefu-migration-plan.md Sec 2.4: resolves via request_log.origin_session_id
    directly -- a stable FK set once, at request-creation time, instead of
    disambiguating by matching wechat_openid (which correctly identified
    the original session over a same-account completion session for Smart
    Robot, but is null/unused for Kefu-originated requests, so that match
    silently returned nothing). The FK approach is channel-agnostic and
    simpler for Smart Robot rows too -- no disambiguation heuristic needed
    at all once the real link exists.
    """
    if target is None:
        return {}
    if target.origin_session_id is None:
        return {}
    from models.session import ConversationSession
    original_session = db.query(ConversationSession).filter_by(
        session_id=target.origin_session_id
    ).first()
    return original_session.collected_fields if original_session else {}


def resolve_completion_target(db: DBSession, reference_serial: str | None):
    """
    Given a reference_serial, resolves (target RequestLog | None, original_fields dict).
    Entry point for callers that only have the serial, not an already-fetched
    RequestLog (e.g. core/confirmation.py's builders, which run before any
    workflow step has executed).
    """
    from models.request_log import RequestLog
    if not reference_serial:
        return None, {}
    target = db.query(RequestLog).filter_by(serial_number=reference_serial).first()
    return target, get_original_fields(db, target)


def member_candidates(db: DBSession, group_id) -> list[dict]:
    """
    kefu-migration-plan.md Sec 2.3: includes both GroupMember and
    kefu_staff rows for the same group, so an admin can see and promote a
    pending Kefu staff member the same way they promote a pending group
    member today. target_identity is the tagged string role_change's
    three boundaries dispatch on (core/role_identity.py) -- a bare
    wechat_openid for Smart Robot rows (identical to pre-Kefu behavior),
    "kefu:<staff_id>" for kefu_staff rows.
    """
    from models.kefu import KefuStaff
    from core.role_identity import tag_kefu_identity

    smart_robot_rows = (
        db.query(GroupMember, Role)
        .join(Role, GroupMember.role_id == Role.role_id)
        .filter(GroupMember.group_id == group_id)
        .all()
    )
    kefu_rows = (
        db.query(KefuStaff, Role)
        .join(Role, KefuStaff.role_id == Role.role_id)
        .filter(KefuStaff.group_id == group_id)
        .all()
    )
    return [
        {
            "target_identity": m.wechat_openid,
            "wechat_openid":   m.wechat_openid,
            "display_name":    m.display_name,
            "role":            r.name,
            "channel":         "smart_robot",
        }
        for m, r in smart_robot_rows
    ] + [
        {
            "target_identity": tag_kefu_identity(s.staff_id),
            "wechat_openid":   None,
            "display_name":    s.display_name,
            "role":            r.name,
            "channel":         "kefu",
        }
        for s, r in kefu_rows
    ]
