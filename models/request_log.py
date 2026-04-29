from sqlalchemy import String, Text, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime


class RequestLog(Base):
    __tablename__ = "request_log"

    log_id:          Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    serial_number:   Mapped[str]              = mapped_column(String(30), nullable=False, unique=True, server_default=text("generate_serial_number()"))
    wechat_openid:   Mapped[str]              = mapped_column(String(128), nullable=False)
    group_id:        Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="SET NULL"))
    service_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="SET NULL"))
    status:          Mapped[str]              = mapped_column(String(20), nullable=False, default="processing")
    raw_message:     Mapped[str]              = mapped_column(Text, nullable=False)
    parsed_input:    Mapped[dict]             = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    result:          Mapped[dict | None]      = mapped_column(JSONB)
    error_detail:    Mapped[str | None]       = mapped_column(Text)
    wechat_msg_id:   Mapped[str | None]       = mapped_column(String(128), unique=True)
    created_at:      Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    completed_at:    Mapped[datetime | None]  = mapped_column(DateTime(timezone=True))
