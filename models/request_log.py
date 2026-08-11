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
    wechat_openid:   Mapped[str | None]       = mapped_column(String(128))
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
    # WeChat Kefu migration (kefu-migration-plan.md Sec 2.4) -- actor/
    # requester split: wechat_openid stays the Smart Robot actor;
    # submitted_by_staff_id is the Kefu actor; customer_id is who the
    # request is *for*, distinct from either. source_channel defaults to
    # 'smart_robot' so existing rows/callers are unaffected.
    customer_id:           Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uchoice_customer.customer_id"))
    submitted_by_staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id"))
    source_channel:        Mapped[str]              = mapped_column(String(20), nullable=False, default="smart_robot")
    origin_session_id:     Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_session.session_id"))
    # kefu-migration-plan.md Sec 7 / Codex round-88 finding 4: set the
    # moment a completion notice is actually delivered in a reply (never
    # at completion time) -- NULL means "not yet shown to anyone".
    completion_notice_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
