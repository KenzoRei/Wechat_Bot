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


def pending_request_candidates(db: DBSession, warehouse_code: str | None, service_type_ids: list[str]) -> list[dict]:
    """
    Requests still awaiting warehouse completion (status='processing') for the
    given service types (inbound or outbound request types), optionally
    scoped to one warehouse (the confirming warehouseman's own).
    """
    if not service_type_ids:
        return []
    query = db.query(RequestLog).filter(
        RequestLog.status == "processing",
        RequestLog.service_type_id.in_(service_type_ids),
    ).order_by(RequestLog.created_at.asc())
    rows = query.all()
    return [
        {
            "serial_number": r.serial_number,
            "wechat_openid": r.wechat_openid,
            "created_at":    r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


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
    original_session = (
        db.query(ConversationSession)
        .filter_by(request_log_id=target.log_id, wechat_openid=target.wechat_openid)
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
