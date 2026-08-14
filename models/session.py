from sqlalchemy import String, Integer, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime


class ConversationSession(Base):
    __tablename__ = "conversation_session"

    session_id:           Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wechat_openid:        Mapped[str | None]       = mapped_column(String(128))
    group_id:             Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="CASCADE"), nullable=False)
    service_type_id:      Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="SET NULL"))
    status:               Mapped[str]              = mapped_column(String(30), nullable=False, default="active")
    conversation_history: Mapped[list]             = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    collected_fields:     Mapped[dict]             = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    request_log_id:       Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now() + INTERVAL '1 hour'"))
    created_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    # source_channel defaults to smart_robot for compatibility with existing
    # rows and callers.
    source_channel:       Mapped[str]              = mapped_column(String(20), nullable=False, default="smart_robot")
    opened_by_staff_id:   Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id"))
    case_revision:        Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    customer_id:          Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uchoice_customer.customer_id"))
    case_number:          Mapped[str | None]       = mapped_column(String(30), unique=True)
