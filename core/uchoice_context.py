"""
Candidate-list context injection for U-Choice — fetches small, scoped
candidate lists fresh on every incoming message, for the AI to fuzzy-match
against in its single-shot call. Not tool-calling: this is pre-fetched
context, the same mechanism as the existing group_context/location_presets
block in ai/prompt_builder.py, just generalized to four more lists.
"""
from sqlalchemy.orm import Session as DBSession
from models.uchoice import UchoiceAddress, UchoiceStorage
from models.request_log import RequestLog
from models.group import GroupMember
from models.role import Role


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
