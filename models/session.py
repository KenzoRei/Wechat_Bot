from sqlalchemy import String, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime


class ConversationSession(Base):
    __tablename__ = "conversation_session"

    session_id:           Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wechat_openid:        Mapped[str]              = mapped_column(String(128), nullable=False)
    group_id:             Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="CASCADE"), nullable=False)
    service_type_id:      Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="SET NULL"))
    status:               Mapped[str]              = mapped_column(String(30), nullable=False, default="active")
    conversation_history: Mapped[list]             = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    collected_fields:     Mapped[dict]             = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    request_log_id:       Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now() + INTERVAL '1 hour'"))
    created_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:           Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
