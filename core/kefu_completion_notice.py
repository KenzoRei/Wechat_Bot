"""
Pending-completion-notice audience tracking. A Kefu-originated inbound or
outbound request's
completion (warehouse confirms it) is surfaced to whichever staff member's
next message touches that request's business/warehouse scope -- not only
the original submitter -- exactly once, via
request_log.completion_notice_shown_at.

Deliberately separate from Smart Robot's own completion notification
(handlers/uchoice/complete_request.py's cross-group webhook push, and
jobs/uchoice_daily.py's scheduled digest) -- those are push mechanisms for
Smart Robot's WeCom groups; this is Kefu's pull-on-next-message mechanism,
scoped to source_channel='kefu' only, per the "pull, not push" strategy.

Split into a lock phase here and a commit phase in core/kefu_case_adapter.py so
the "shown" mark
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
    to any of this staff member's own assigned warehouses (unscoped for
    admin/accountant, who see every pending notice). Returns the locked
    RequestLog row, or
    None if there's nothing pending or another transaction already holds
    the only matching row's lock. Caller is responsible for setting
    completion_notice_shown_at and committing, atomically with whatever
    else this turn's transaction includes.
    """
    from models.request_log import RequestLog

    if staff.warehouse_codes is None:
        row = db.execute(sql_text(
            """
            SELECT rl.log_id FROM request_log rl
            JOIN service_type st ON st.service_type_id = rl.service_type_id
            WHERE rl.status = 'success' AND rl.source_channel = 'kefu'
              AND rl.completed_at IS NOT NULL
              AND rl.completion_notice_shown_at IS NULL
              AND st.name IN ('uchoice_inbound_request', 'uchoice_outbound_request')
            ORDER BY rl.completed_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )).first()
    else:
        row = db.execute(sql_text(
            """
            SELECT rl.log_id FROM request_log rl
            JOIN service_type st ON st.service_type_id = rl.service_type_id
            WHERE rl.status = 'success' AND rl.source_channel = 'kefu'
              AND rl.completed_at IS NOT NULL
              AND rl.completion_notice_shown_at IS NULL
              AND st.name IN ('uchoice_inbound_request', 'uchoice_outbound_request')
              AND rl.result ->> 'warehouse_code' = ANY(:whs)
            ORDER BY rl.completed_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ), {"whs": staff.warehouse_codes}).first()

    if row is None:
        return None
    return db.get(RequestLog, row.log_id)


def notice_text(db: DBSession, log) -> str:
    direction_label = _direction_label(db, log.service_type_id)
    from core.kefu_outcomes import CompletionNoticeOutcome
    from core.kefu_response_renderer import render_kefu_outcome
    return render_kefu_outcome(CompletionNoticeOutcome(
        serial_number=log.serial_number,
        direction_label=direction_label,
    ))


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
    raise ValueError("completion notice is only valid for inbound/outbound requests")
