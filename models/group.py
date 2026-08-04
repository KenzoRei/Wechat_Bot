from sqlalchemy import String, Text, Boolean, Integer, ForeignKey, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
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
    warehouse_code: Mapped[str | None]  = mapped_column(String(20))
    is_active:     Mapped[bool]         = mapped_column(Boolean, nullable=False, default=True)
    joined_at:     Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:    Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GroupService(Base):
    __tablename__ = "group_service"

    group_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="CASCADE"), primary_key=True)
    service_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="CASCADE"), primary_key=True)
    workflow_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.workflow_id", ondelete="RESTRICT"), nullable=False)
    config:          Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class GroupServiceRole(Base):
    """
    Deny-by-default permission grant: a (group_id, service_type_id) pair is
    invisible to a role unless a matching row exists here.
    Composite FK to group_service ensures you can't grant access to a service
    the group was never assigned in the first place.
    """
    __tablename__ = "group_service_role"

    group_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    service_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    role_id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("role.role_id", ondelete="CASCADE"), primary_key=True)
    created_by:      Mapped[str]       = mapped_column(String(128), nullable=False)
    created_at:      Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    # Composite FK (group_id, service_type_id) -> group_service(group_id, service_type_id)
    # is declared in the migration SQL, not here — ORM doesn't need it for queries.
