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


def sku_catalog(db: DBSession) -> list[dict]:
    """
    All 8 U-Choice SKUs — cheap, full table every time (no scoping needed).
    Lets the AI resolve a free-text product description (e.g. "2寸透明胶带")
    to the real sku_code (e.g. "t4") instead of inventing one from the
    customer's own words.
    """
    rows = db.query(UchoiceSku).all()
    return [{"sku_code": s.sku_code, "description": s.description} for s in rows]


def sku_label_map(db: DBSession) -> dict[str, str]:
    """
    sku_code -> human-readable description, e.g. 't4' -> 'T4 2-inch Clear
    Packing Tape'. Shared by core/confirmation.py and core/result_message.py
    so both resolve product names identically rather than duplicating the
    lookup query.
    """
    return {s.sku_code: s.description for s in db.query(UchoiceSku).all()}


def address_candidates(db: DBSession) -> list[dict]:
    rows = db.query(UchoiceAddress).all()
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
                candidate["destination"] = f"{addr.company_name}（{addr.addr}）"

        candidates.append(candidate)

    return candidates


def resolve_default_bucket(db: DBSession, warehouse_code: str | None, sku_code: str) -> int | None:
    """
    Largest-pallet-count bucket for a sku+warehouse — the "propose the
    largest available bucket as a default" rule for an outbound line missing
    boxes_per_pallet. Shared by the confirmation-display resolver and the
    session-mutating resolver in workflow_engine so both pick the same
    default instead of maintaining two copies of this query.
    """
    bucket = (
        db.query(UchoiceStorage)
        .filter_by(warehouse_code=warehouse_code, sku_code=sku_code)
        .order_by(UchoiceStorage.pallet_count.desc())
        .first()
    )
    return bucket.boxes_per_pallet if bucket else None


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


def storage_bucket_candidates(db: DBSession, warehouse_code: str | None) -> list[dict]:
    query = db.query(UchoiceStorage)
    if warehouse_code:
        query = query.filter(UchoiceStorage.warehouse_code == warehouse_code)
    rows = query.all()
    return [
        {
            "warehouse_code":   s.warehouse_code,
            "sku_code":         s.sku_code,
            "boxes_per_pallet": s.boxes_per_pallet,
            "pallet_count":     s.pallet_count,
        }
        for s in rows
    ]


def get_original_fields(db: DBSession, target) -> dict:
    """
    Given an already-fetched target RequestLog, resolves its original
    submitter's collected_fields (warehouse_code, sku_lines, etc.) via their
    ConversationSession. Shared by LookupAndValidateCompletionHandler
    (execution-time) and core/confirmation.py's completion builders
    (confirm-time display) so both resolve the original request identically.
    """
    from models.session import ConversationSession
    if target is None:
        return {}
    # Must also filter by the target's own service_type_id (the original
    # service, e.g. uchoice_inbound_request) — the completion session itself
    # (confirm_inbound_completion) gets request_log_id set to this same
    # target log too (that's how targets_existing_request linking works), so
    # filtering on request_log_id + wechat_openid alone can match it as well
    # whenever the submitter and the confirmer share an account. Without this,
    # order_by(created_at DESC).first() picks the newer completion session —
    # whose collected_fields is just {"reference_serial": ...} — instead of
    # the actual original submission.
    original_session = (
        db.query(ConversationSession)
        .filter_by(
            request_log_id=target.log_id,
            wechat_openid=target.wechat_openid,
            service_type_id=target.service_type_id,
        )
        .order_by(ConversationSession.created_at.desc())
        .first()
    )
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
    rows = (
        db.query(GroupMember, Role)
        .join(Role, GroupMember.role_id == Role.role_id)
        .filter(GroupMember.group_id == group_id)
        .all()
    )
    return [
        {
            "wechat_openid": m.wechat_openid,
            "display_name":  m.display_name,
            "role":          r.name,
        }
        for m, r in rows
    ]
