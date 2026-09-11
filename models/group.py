from sqlalchemy import String, Text, Boolean, Integer, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from database import Base
import uuid
from datetime import datetime


class GroupConfig(Base):
    __tablename__ = "group_config"

    group_id:            Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wechat_group_id:     Mapped[str]          = mapped_column(String(128), nullable=False, unique=True)
    description:         Mapped[str | None]   = mapped_column(String(500))
    is_active:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=True)
    daily_request_limit: Mapped[int | None]    = mapped_column(Integer)
    context:             Mapped[dict | None]  = mapped_column(JSONB)
    group_robot_webhook_url: Mapped[str | None] = mapped_column(Text)
    created_at:          Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:          Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GroupMember(Base):
    __tablename__ = "group_member"

    wechat_openid: Mapped[str]          = mapped_column(String(128), primary_key=True, nullable=False)
    group_id:      Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="CASCADE"), primary_key=True)
    role_id:       Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), ForeignKey("role.role_id", ondelete="RESTRICT"), nullable=False)
    display_name:  Mapped[str | None]   = mapped_column(String(200))
    warehouse_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))
    # Set only for role='customer' members (enforced at the application
    # layer, not a DB constraint -- mirrors the warehouseman/warehouse_codes
    # pattern below). This binding is authoritative once set: nothing
    # extracted from conversation may override it. Named billing_customer_id,
    # not customer_id, to avoid colliding with the unrelated, pre-existing
    # customer_id concept (request_log.customer_id -> uchoice_customer).
    billing_customer_id: Mapped[str | None] = mapped_column(String(7), ForeignKey("customer.customer_id"))
    is_active:     Mapped[bool]         = mapped_column(Boolean, nullable=False, default=True)
    joined_at:     Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:    Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GroupService(Base):
    __tablename__ = "group_service"

    group_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="CASCADE"), primary_key=True)
    service_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="CASCADE"), primary_key=True)
    workflow_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.workflow_id", ondelete="RESTRICT"), nullable=False)
    config:          Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


    # GroupServiceRole (per-group role->service grants) was removed in V30 --
    # replaced by the GLOBAL models.role.RoleServicePermission. See that
    # class's docstring for why: a role represents a job function that
    # should behave identically regardless of which tenant/group a person
    # belongs to; the per-group axis added no real differentiation in
    # practice, just per-group admin busywork. Real multi-tenancy (which
    # services a tenant has access to at all) still lives in GroupService
    # above, untouched by this change.
