"""
Fabricated or malformed sku_lines must not merge into persisted
collected_fields; catching them only later at
pre-confirm time. A bad entry that's already stored could be re-serialized
into the next turn's AI prompt even if it's never shown to the customer.
"""
import pytest
from sqlalchemy import text

from database import SessionLocal
from core import access_control, session_manager, workflow_engine
from ai.base import AIResponse

OPENID = "transworld"
WECHAT_GROUP_ID = "wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A"


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


_created_session_ids: list = []
_created_log_ids: list = []


@pytest.fixture(autouse=True)
def cleanup(db):
    yield
    for lid in _created_log_ids:
        db.execute(text("delete from request_log where log_id = :lid"), {"lid": lid})
    for sid in _created_session_ids:
        db.execute(text("delete from conversation_session where session_id = :sid"), {"sid": sid})
    _created_session_ids.clear()
    _created_log_ids.clear()
    db.commit()


def test_fabricated_sku_line_never_persisted_on_new_request(db):
    existing = db.execute(text(
        "select session_id from conversation_session where wechat_openid = :o "
        "and status in ('active','pending_confirmation')"
    ), {"o": OPENID}).scalar()
    if existing is not None:
        pytest.fail(f"identity already has an active session ({existing})")

    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    message = {"content": "test", "msg_id": None, "response_url": ""}
    context = session_manager.build_context(db, access, None, message)

    ai_response = AIResponse(
        intent="new_request", reply="test",
        extracted_fields={
            "sku_lines": [
                {"sku_code": "s4", "box_count": 10},       # valid
                {"sku_code": "fabricated-nonexistent", "box_count": 5},  # invalid: not in catalog
                {"box_count": 3},                          # invalid: no sku_code at all
            ],
        },
        all_fields_collected=False,
        service_type_name="uchoice_outbound_request",
    )

    workflow_engine._handle_new_request(context, ai_response, db)

    session_id = context.get("session_id")
    assert session_id is not None
    _created_session_ids.append(session_id)
    from models.session import ConversationSession
    session = db.query(ConversationSession).filter_by(session_id=session_id).first()
    _created_log_ids.append(session.request_log_id)

    stored_lines = session.collected_fields.get("sku_lines", [])
    stored_skus = [l.get("sku_code") for l in stored_lines]
    assert stored_skus == ["s4"], (
        f"only the valid line should have been persisted, got {stored_skus!r} -- "
        "a fabricated or missing sku_code must never reach collected_fields at all"
    )


@pytest.mark.parametrize("malformed_value", ["not a list", {"sku_code": "s1"}, None, 42])
def test_malformed_non_list_sku_lines_never_persisted(db, malformed_value):
    """A non-list sku_lines shape is omitted rather than persisted unchanged."""
    existing = db.execute(text(
        "select session_id from conversation_session where wechat_openid = :o "
        "and status in ('active','pending_confirmation')"
    ), {"o": OPENID}).scalar()
    if existing is not None:
        pytest.fail(f"identity already has an active session ({existing})")

    access = access_control.check_access(db, wechat_openid=OPENID, wechat_group_id=WECHAT_GROUP_ID)
    message = {"content": "test", "msg_id": None, "response_url": ""}
    context = session_manager.build_context(db, access, None, message)

    ai_response = AIResponse(
        intent="new_request", reply="test",
        extracted_fields={"sku_lines": malformed_value},
        all_fields_collected=False,
        service_type_name="uchoice_outbound_request",
    )

    workflow_engine._handle_new_request(context, ai_response, db)

    session_id = context.get("session_id")
    assert session_id is not None
    _created_session_ids.append(session_id)
    from models.session import ConversationSession
    session = db.query(ConversationSession).filter_by(session_id=session_id).first()
    _created_log_ids.append(session.request_log_id)

    assert "sku_lines" not in session.collected_fields, (
        f"malformed sku_lines ({malformed_value!r}) must be omitted entirely, "
        f"not persisted as-is; got {session.collected_fields!r}"
    )
