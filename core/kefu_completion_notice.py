"""
Pending-completion-notice audience tracking (kefu-migration-plan.md Sec 7,
Codex round-88 finding 4). A Kefu-originated inbound/outbound request's
completion (warehouse confirms it) is surfaced to whichever staff member's
next message touches that request's business/warehouse scope -- not only
the original submitter -- exactly once, via
request_log.completion_notice_shown_at.

Deliberately separate from Smart Robot's own completion notification
(handlers/uchoice/complete_request.py's cross-group webhook push, and
jobs/uchoice_daily.py's scheduled digest) -- those are push mechanisms for
Smart Robot's WeCom groups; this is Kefu's pull-on-next-message mechanism,
scoped to source_channel='kefu' only, per the "pull, not push" strategy.

Codex round-90 finding 6: split into a lock phase (this module) and a
commit phase (the caller, core/kefu_case_adapter.py) so the "shown" mark
lands in the SAME transaction/commit as the reply that actually carries
it -- a delivery failure must not permanently lose the notice, and two
concurrent turns must never both claim the same row. `SELECT ... FOR
UPDATE SKIP LOCKED` gives both: a genuine row lock (not the prior
read-then-write race), and a losing concurrent claimant sees nothing
rather than blocking.
"""
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session as DBSession


def lock_pending_completion_notice(db: DBSession, staff):
    """
    Locks (within the CALLER's still-open transaction -- does not commit)
    the oldest not-yet-notified Kefu-originated completed request scoped
    to this staff member's own warehouse (unscoped for admin/accountant,
    who see every pending notice). Returns the locked RequestLog row, or
    None if there's nothing pending or another transaction already holds
    the only matching row's lock. Caller is responsible for setting
    completion_notice_shown_at and committing, atomically with whatever
    else this turn's transaction includes.
    """
    from models.request_log import RequestLog

    if staff.warehouse_code is None:
        row = db.execute(sql_text(
            """
            SELECT log_id FROM request_log
            WHERE status = 'success' AND source_channel = 'kefu'
              AND completion_notice_shown_at IS NULL
            ORDER BY completed_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )).first()
    else:
        row = db.execute(sql_text(
            """
            SELECT log_id FROM request_log
            WHERE status = 'success' AND source_channel = 'kefu'
              AND completion_notice_shown_at IS NULL
              AND result ->> 'warehouse_code' = :wh
            ORDER BY completed_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ), {"wh": staff.warehouse_code}).first()

    if row is None:
        return None
    return db.get(RequestLog, row.log_id)


def notice_text(db: DBSession, log) -> str:
    direction_label = _direction_label(db, log.service_type_id)
    return f"提示：申请 {log.serial_number}（{direction_label}）已由仓库确认完成。"


def _direction_label(db: DBSession, service_type_id) -> str:
    """
    A targets_existing_request completion (confirm_inbound_completion/
    confirm_outbound_completion) updates the ORIGINAL request_log row in
    place (core/workflow_engine.py reassigns session.request_log_id to the
    target before calling mark_success) -- service_type_id on that row is
    still the original uchoice_inbound_request/uchoice_outbound_request,
    never reassigned to the completion service's own type.
    """
    from models.service import ServiceType

    st = db.query(ServiceType).filter_by(service_type_id=service_type_id).first() if service_type_id else None
    if st and st.name == "uchoice_inbound_request":
        return "入库"
    if st and st.name == "uchoice_outbound_request":
        return "出库"
    return "请求"
