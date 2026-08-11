from sqlalchemy import String, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from database import Base
import uuid
from datetime import datetime


class InteractionLog(Base):
    """
    Write-once, append-only. One row per incoming message once intent is
    classified, regardless of outcome — separate from request_log, which has
    stricter lookup/update needs. Used for funnel/efficiency analysis.
    """
    __tablename__ = "interaction_log"

    interaction_id:  Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wechat_openid:   Mapped[str | None]       = mapped_column(String(128))
    group_id:        Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="SET NULL"))
    intent:          Mapped[str]              = mapped_column(String(30), nullable=False)
    intent_type:     Mapped[str]              = mapped_column(String(20), nullable=False)
    service_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="SET NULL"))
    request_log_id:  Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("request_log.log_id", ondelete="SET NULL"))
    created_at:      Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    # WeChat Kefu migration (kefu-migration-plan.md Sec 2.4)
    customer_id:           Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uchoice_customer.customer_id"))
    submitted_by_staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id"))
    source_channel:        Mapped[str]              = mapped_column(String(20), nullable=False, default="smart_robot")
