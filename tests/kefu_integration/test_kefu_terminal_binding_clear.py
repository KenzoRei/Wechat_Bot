import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.kefu_case_adapter import _finalize_turn
from database import SessionLocal
from models.kefu import KefuStaff, KefuStaffCaseContext
from models.session import ConversationSession


def test_terminal_case_clears_every_staff_binding():
    from models.group import GroupConfig
    from models.role import Role

    db = SessionLocal()
    staff_ids = []
    session_id = None
    try:
        group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
        role = db.query(Role).filter_by(name="admin").one()
        staff = []
        for label in ("actor", "observer"):
            row = KefuStaff(
                open_kfid=f"kf-binding-{label}-{uuid.uuid4().hex[:8]}",
                external_userid=f"binding-{label}-{uuid.uuid4().hex[:8]}",
                group_id=group.group_id,
                role_id=role.role_id,
            )
            db.add(row)
            db.flush()
            staff.append(row)
            staff_ids.append(row.staff_id)
        session = ConversationSession(
            wechat_openid=None,
            group_id=group.group_id,
            service_type_id=None,
            status="completed",
            conversation_history=[],
            collected_fields={},
            source_channel="kefu",
            opened_by_staff_id=staff[0].staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(session)
        db.flush()
        session_id = session.session_id
        db.add_all([
            KefuStaffCaseContext(staff_id=row.staff_id, active_session_id=session.session_id)
            for row in staff
        ])
        db.commit()

        _finalize_turn(
            db, client=None, staff=staff[0], session_id=session.session_id,
            message_content="finish", msgid=f"binding-{uuid.uuid4().hex}",
            reply_text="done", context={},
        )
        db.commit()

        bindings = db.execute(text(
            "select active_session_id from kefu_staff_case_context where staff_id=any(:staff_ids)"
        ), {"staff_ids": staff_ids}).scalars().all()
        assert bindings == [None, None]
    finally:
        db.rollback()
        if staff_ids:
            db.execute(text("delete from case_turn where acting_staff_id=any(:ids) or session_id=:sid"), {"ids": staff_ids, "sid": session_id})
            db.execute(text("delete from kefu_outbound_delivery where recipient_staff_id=any(:ids)"), {"ids": staff_ids})
            db.execute(text("delete from kefu_staff_case_context where staff_id=any(:ids)"), {"ids": staff_ids})
        if session_id:
            db.execute(text("delete from conversation_session where session_id=:sid"), {"sid": session_id})
        if staff_ids:
            db.execute(text("delete from kefu_staff where staff_id=any(:ids)"), {"ids": staff_ids})
        db.commit()
        db.close()
